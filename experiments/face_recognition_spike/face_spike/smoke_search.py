from __future__ import annotations

import html
import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import quote

import numpy as np
from numpy.typing import NDArray

from .analysis import BoundingBox
from .benchmark import BenchmarkProposal, BenchmarkQuery
from .index import FaceIndex

DIRECT_COSINE_THRESHOLD = 0.363


@dataclass(frozen=True)
class SmokeSearchPhotoResult:
    rank: int
    filename: str
    face_id: str
    bounding_box: BoundingBox
    cosine_distance: float


@dataclass(frozen=True)
class SmokeSearchQueryResult:
    query_id: str
    query_face_id: str
    query_filename: str
    query_crop_path: str
    embedding_seconds: float
    search_seconds: float
    results: tuple[SmokeSearchPhotoResult, ...]


@dataclass(frozen=True)
class SmokeSearchResult:
    proposal: BenchmarkProposal
    index: FaceIndex
    query_count: int
    limit: int
    queries: tuple[SmokeSearchQueryResult, ...]


@dataclass(frozen=True, eq=False)
class SearchComparisonQuery:
    query_id: str
    source_filename: str
    source_run_sha256: str
    query_crop_sha256: str
    embedding: NDArray[np.float32]
    confirmed_relevant: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.query_id
            or not self.source_filename
            or any(
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                for value in (self.source_run_sha256, self.query_crop_sha256)
            )
        ):
            raise ValueError("search comparison query identity is invalid")
        vector = _normalized_query_vector(self.embedding)
        if tuple(sorted(set(self.confirmed_relevant))) != self.confirmed_relevant:
            raise ValueError("confirmed relevant filenames must be unique and ordered")
        if self.source_filename in self.confirmed_relevant:
            raise ValueError("held-out photo cannot be a confirmed result")
        object.__setattr__(self, "embedding", vector)


@dataclass(frozen=True)
class SearchComparisonPhoto:
    face_id: str
    filename: str

    def __post_init__(self) -> None:
        if not self.face_id or not self.filename:
            raise ValueError("search comparison photo is invalid")


@dataclass(frozen=True)
class SearchComparisonQueryResult:
    query_id: str
    source_run_sha256: str
    query_crop_sha256: str
    baseline_results: tuple[SearchComparisonPhoto, ...]
    candidate_results: tuple[SearchComparisonPhoto, ...]
    confirmed_relevant: tuple[str, ...]
    lost_confirmed_relevant: tuple[str, ...]
    quality_rejected_supports: tuple[SearchComparisonPhoto, ...]

    def __post_init__(self) -> None:
        hashes = (self.source_run_sha256, self.query_crop_sha256)
        baseline_names = self.baseline_top
        candidate_names = self.candidate_top
        expected_lost = tuple(
            sorted(set(self.confirmed_relevant) & set(baseline_names) - set(candidate_names))
        )
        supports = self.quality_rejected_supports
        if (
            not self.query_id
            or any(
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                for value in hashes
            )
            or tuple(sorted(set(self.confirmed_relevant))) != self.confirmed_relevant
            or len(baseline_names) != len(set(baseline_names))
            or len(candidate_names) != len(set(candidate_names))
            or len({item.face_id for item in self.baseline_results}) != len(self.baseline_results)
            or len({item.face_id for item in self.candidate_results}) != len(self.candidate_results)
            or self.lost_confirmed_relevant != expected_lost
            or len({item.filename for item in supports}) != len(supports)
            or any(item not in self.baseline_results for item in supports)
            or any(item.filename not in expected_lost for item in supports)
        ):
            raise ValueError("search comparison query result does not reconcile")

    @property
    def baseline_top(self) -> tuple[str, ...]:
        return tuple(item.filename for item in self.baseline_results)

    @property
    def candidate_top(self) -> tuple[str, ...]:
        return tuple(item.filename for item in self.candidate_results)

    @property
    def lost_due_to_quality_rejection(self) -> tuple[str, ...]:
        return tuple(item.filename for item in self.quality_rejected_supports)


@dataclass(frozen=True)
class SearchComparison:
    quality_comparison_sha256: str
    benchmark_sha256: str
    baseline_index_sha256: str
    candidate_index_sha256: str
    direct_threshold: float
    query_results: tuple[SearchComparisonQueryResult, ...]

    def __post_init__(self) -> None:
        hashes = (
            self.quality_comparison_sha256,
            self.benchmark_sha256,
            self.baseline_index_sha256,
            self.candidate_index_sha256,
        )
        if (
            any(
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                for value in hashes
            )
            or self.direct_threshold != DIRECT_COSINE_THRESHOLD
        ):
            raise ValueError("search comparison source identity is invalid")
        query_ids = tuple(item.query_id for item in self.query_results)
        if not query_ids or query_ids != tuple(sorted(set(query_ids))):
            raise ValueError("search comparison queries are invalid")

    @property
    def approved(self) -> bool:
        return not any(result.lost_confirmed_relevant for result in self.query_results)

    @property
    def aggregate(self) -> dict[str, int]:
        baseline_unique = sum(len(result.baseline_top) for result in self.query_results)
        candidate_unique = sum(len(result.candidate_top) for result in self.query_results)
        values = {
            "queries": len(self.query_results),
            "baseline_unique_results": baseline_unique,
            "candidate_unique_results": candidate_unique,
            "unique_photo_delta": candidate_unique - baseline_unique,
        }
        for limit in (1, 5, 10):
            values[f"baseline_top_{limit}_relevant"] = sum(
                len(set(result.baseline_top[:limit]) & set(result.confirmed_relevant))
                for result in self.query_results
            )
            values[f"candidate_top_{limit}_relevant"] = sum(
                len(set(result.candidate_top[:limit]) & set(result.confirmed_relevant))
                for result in self.query_results
            )
        values["lost_confirmed_relevant"] = sum(
            len(result.lost_confirmed_relevant) for result in self.query_results
        )
        return values


def rank_unique_photos(
    query_embedding: NDArray[np.float32],
    index: FaceIndex,
    held_out_filename: str,
    *,
    limit: int,
    maximum_distance: float | None = None,
) -> tuple[SmokeSearchPhotoResult, ...]:
    """Rank one best face per photo with exact cosine distance."""
    if not isinstance(index, FaceIndex):
        raise TypeError("index must be a FaceIndex")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("result limit must be positive")
    if maximum_distance is not None and (
        not math.isfinite(maximum_distance) or not 0 <= maximum_distance <= 2
    ):
        raise ValueError("maximum distance is invalid")
    vector = _normalized_query_vector(query_embedding)
    if vector.shape[0] != index.embeddings.shape[1]:
        raise ValueError("query embedding dimension does not match index")

    included = np.asarray(
        [entry.filename != held_out_filename for entry in index.entries], dtype=bool
    )
    if not included.any():
        raise ValueError("query holdout leaves no gallery face")
    matrix = index.embeddings[included]
    entries = tuple(
        entry for entry, include in zip(index.entries, included, strict=True) if include
    )
    distances = np.asarray(1.0 - matrix @ vector, dtype=np.float32)
    face_ids = np.asarray([entry.face_id for entry in entries], dtype=str)
    ordered_indices = np.lexsort((face_ids, distances))

    best_by_filename: dict[str, tuple[float, int]] = {}
    for raw_index in ordered_indices:
        index_position = int(raw_index)
        entry = entries[index_position]
        if maximum_distance is not None and distances[index_position] > maximum_distance:
            continue
        best_by_filename.setdefault(
            entry.filename, (float(distances[index_position]), index_position)
        )
    selected = sorted(
        best_by_filename.values(),
        key=lambda item: (item[0], entries[item[1]].filename),
    )[:limit]
    return tuple(
        SmokeSearchPhotoResult(
            rank=rank,
            filename=entries[index_position].filename,
            face_id=entries[index_position].face_id,
            bounding_box=entries[index_position].bounding_box,
            cosine_distance=distance,
        )
        for rank, (distance, index_position) in enumerate(selected, start=1)
    )


def compare_search_indexes(
    queries: tuple[SearchComparisonQuery, ...],
    baseline_index: FaceIndex,
    candidate_index: FaceIndex,
    *,
    quality_rejected_baseline_face_ids: tuple[str, ...],
    quality_comparison_sha256: str,
    benchmark_sha256: str,
    baseline_index_sha256: str,
    candidate_index_sha256: str,
) -> SearchComparison:
    """Run the same closed queries against two indexes with production ranking semantics."""
    if not queries or len({query.query_id for query in queries}) != len(queries):
        raise ValueError("search comparison queries must be nonempty and unique")
    if not isinstance(baseline_index, FaceIndex) or not isinstance(candidate_index, FaceIndex):
        raise TypeError("search comparison requires FaceIndex values")
    rejected = tuple(sorted(set(quality_rejected_baseline_face_ids)))
    if rejected != quality_rejected_baseline_face_ids:
        raise ValueError("quality-rejected face IDs must be unique and ordered")
    results: list[SearchComparisonQueryResult] = []
    for query in queries:
        baseline = rank_unique_photos(
            query.embedding,
            baseline_index,
            query.source_filename,
            limit=len(baseline_index.entries),
            maximum_distance=DIRECT_COSINE_THRESHOLD,
        )
        candidate = rank_unique_photos(
            query.embedding,
            candidate_index,
            query.source_filename,
            limit=len(candidate_index.entries),
            maximum_distance=DIRECT_COSINE_THRESHOLD,
        )
        baseline_names = tuple(item.filename for item in baseline)
        candidate_names = tuple(item.filename for item in candidate)
        lost = tuple(
            sorted(set(query.confirmed_relevant) & set(baseline_names) - set(candidate_names))
        )
        results.append(
            SearchComparisonQueryResult(
                query.query_id,
                query.source_run_sha256,
                query.query_crop_sha256,
                tuple(SearchComparisonPhoto(item.face_id, item.filename) for item in baseline),
                tuple(SearchComparisonPhoto(item.face_id, item.filename) for item in candidate),
                query.confirmed_relevant,
                lost,
                tuple(
                    SearchComparisonPhoto(item.face_id, item.filename)
                    for item in baseline
                    if item.filename in lost and item.face_id in rejected
                ),
            )
        )
    return SearchComparison(
        quality_comparison_sha256,
        benchmark_sha256,
        baseline_index_sha256,
        candidate_index_sha256,
        DIRECT_COSINE_THRESHOLD,
        tuple(results),
    )


def run_smoke_search(
    proposal: BenchmarkProposal,
    index: FaceIndex,
    query_processor: Callable[[BenchmarkQuery], NDArray[np.float32]],
    *,
    query_count: int,
    limit: int,
) -> SmokeSearchResult:
    """Process the first bounded proposal queries before publishing any output."""
    if not isinstance(proposal, BenchmarkProposal):
        raise TypeError("proposal must be a BenchmarkProposal")
    if not isinstance(index, FaceIndex):
        raise TypeError("index must be a FaceIndex")
    if (
        isinstance(query_count, bool)
        or not isinstance(query_count, int)
        or not 5 <= query_count <= 10
    ):
        raise ValueError("query count must be between 5 and 10")
    if len(proposal.queries) < query_count:
        raise ValueError("proposal does not contain enough queries")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("result limit must be positive")

    query_results: list[SmokeSearchQueryResult] = []
    for query in proposal.queries[:query_count]:
        embedding_start = perf_counter()
        embedding = _normalized_query_vector(query_processor(query))
        embedding_seconds = perf_counter() - embedding_start
        search_start = perf_counter()
        results = rank_unique_photos(embedding, index, query.query_filename, limit=limit)
        search_seconds = perf_counter() - search_start
        query_results.append(
            SmokeSearchQueryResult(
                query_id=query.query_id,
                query_face_id=query.query_face_id,
                query_filename=query.query_filename,
                query_crop_path=query.query_crop_path,
                embedding_seconds=embedding_seconds,
                search_seconds=search_seconds,
                results=results,
            )
        )
    return SmokeSearchResult(proposal, index, query_count, limit, tuple(query_results))


def write_smoke_search_output(
    output: Path,
    result: SmokeSearchResult,
    run_root: Path,
    photos_root: Path,
) -> None:
    """Publish the compact report only after every selected query succeeds."""
    if not isinstance(result, SmokeSearchResult):
        raise TypeError("result must be a SmokeSearchResult")
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.smoke-search.", dir=output.parent))
    try:
        (staging / "results.json").write_text(
            json.dumps(_result_payload(result), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        (staging / "report.html").write_text(
            _render_report(result, staging, run_root, photos_root), encoding="utf-8"
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _normalized_query_vector(value: NDArray[np.float32]) -> NDArray[np.float32]:
    vector = np.asarray(value)
    if vector.dtype == object or vector.ndim != 1 or not np.issubdtype(vector.dtype, np.floating):
        raise ValueError("query embedding must be a floating-point vector")
    normalized = np.ascontiguousarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(normalized))
    if not normalized.size or not np.isfinite(normalized).all() or not math.isfinite(norm):
        raise ValueError("query embedding must be finite and nonempty")
    if not np.isclose(norm, 1.0, rtol=1e-5, atol=1e-6):
        raise ValueError("query embedding must be normalized")
    return normalized


def _result_payload(result: SmokeSearchResult) -> dict[str, object]:
    manifest = result.index.manifest
    return {
        "index": {
            "sface_model": dict(manifest.sface_model),
            "yunet_model": dict(manifest.yunet_model),
        },
        "limit": result.limit,
        "query_count": result.query_count,
        "queries": [
            {
                "embedding_seconds": query.embedding_seconds,
                "query_crop_path": query.query_crop_path,
                "query_face_id": query.query_face_id,
                "query_filename": query.query_filename,
                "query_id": query.query_id,
                "results": [
                    {
                        "bounding_box": {
                            "height": match.bounding_box.height,
                            "width": match.bounding_box.width,
                            "x": match.bounding_box.x,
                            "y": match.bounding_box.y,
                        },
                        "cosine_distance": match.cosine_distance,
                        "face_id": match.face_id,
                        "filename": match.filename,
                        "rank": match.rank,
                    }
                    for match in query.results
                ],
                "search_seconds": query.search_seconds,
            }
            for query in result.queries
        ],
        "source": {
            "faces_sha256": result.proposal.source.faces_sha256,
            "index_manifest_sha256": result.proposal.source.index_manifest_sha256,
            "proposal_sha256": result.proposal.source.proposal_sha256,
            "run_manifest_sha256": result.proposal.source.run_manifest_sha256,
        },
    }


def _render_report(
    result: SmokeSearchResult, output: Path, run_root: Path, photos_root: Path
) -> str:
    sections = "".join(
        _render_query(query, output, run_root, photos_root) for query in result.queries
    )
    return (
        '<!doctype html>\n<html><head><meta charset="utf-8"><title>Smoke search</title>'
        "<style>img{max-width:320px;height:auto}.photo-with-box{position:relative;display:inline-block}"
        ".face-box-overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}"
        ".face-box-overlay rect{fill:none;stroke:#e11;stroke-width:3}</style></head>"
        "<body><h1>Local selfie search smoke report</h1>"
        + sections
        + "<script>for(const overlay of document.querySelectorAll('.face-box-overlay')){"
        "const image=overlay.parentElement.querySelector('img');const draw=()=>{"
        "const [x,y,width,height]=overlay.dataset.boundingBox.split(',').map(Number);"
        "overlay.setAttribute('viewBox',`0 0 ${image.naturalWidth} ${image.naturalHeight}`);"
        'overlay.innerHTML=`<rect x="${x}" y="${y}" width="${width}" height="${height}"/>`;'
        "};if(image.complete){draw()}else{image.addEventListener('load',draw)}};</script>"
        "</body></html>\n"
    )


def _render_query(
    query: SmokeSearchQueryResult, output: Path, run_root: Path, photos_root: Path
) -> str:
    query_href = _relative_href(output, run_root / query.query_crop_path)
    query_source = html.escape(query_href, quote=True)
    query_alt = html.escape(query.query_face_id, quote=True)
    rows = "".join(_render_match(match, output, photos_root) for match in query.results)
    return (
        "<section>"
        f"<h2>{html.escape(query.query_id)}</h2>"
        f'<figure><img class="query-crop" src="{query_source}" alt="Query crop for {query_alt}">'
        f"<figcaption>Query crop · source: {html.escape(query.query_filename)}"
        "</figcaption></figure>"
        f"<ol>{rows}</ol></section>"
    )


def _render_match(match: SmokeSearchPhotoResult, output: Path, photos_root: Path) -> str:
    photo_href = html.escape(_relative_href(output, photos_root / match.filename), quote=True)
    photo_alt = html.escape(match.filename, quote=True)
    box = ",".join(
        f"{value:g}"
        for value in (
            match.bounding_box.x,
            match.bounding_box.y,
            match.bounding_box.width,
            match.bounding_box.height,
        )
    )
    return (
        "<li>"
        '<figure><div class="photo-with-box">'
        f'<img class="result-photo" src="{photo_href}" alt="{photo_alt}">'
        f'<svg class="face-box-overlay" data-bounding-box="{box}" '
        f'aria-label="Matched face box {box}"></svg></div><figcaption>'
        f'<a href="{photo_href}">{html.escape(match.filename)}</a> — rank {match.rank}, '
        f"{html.escape(match.face_id)}, distance {match.cosine_distance:.6f}, box "
        f"({match.bounding_box.x:g}, {match.bounding_box.y:g}, {match.bounding_box.width:g}, "
        f"{match.bounding_box.height:g})</figcaption></figure></li>"
    )


def _relative_href(output: Path, target: Path) -> str:
    return quote(Path(os.path.relpath(target, start=output)).as_posix(), safe="/-._~")
