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


def rank_unique_photos(
    query_embedding: NDArray[np.float32],
    index: FaceIndex,
    held_out_filename: str,
    *,
    limit: int,
) -> tuple[SmokeSearchPhotoResult, ...]:
    """Rank one best face per photo with exact cosine distance."""
    if not isinstance(index, FaceIndex):
        raise TypeError("index must be a FaceIndex")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("result limit must be positive")
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
