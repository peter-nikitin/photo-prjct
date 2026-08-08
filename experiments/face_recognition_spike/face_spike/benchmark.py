from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Literal

import numpy as np

from .index import FaceIndex

SCHEMA_VERSION = 1
NEAREST_CROSS_CLUSTER_COUNT = 6
DISTANT_CROSS_CLUSTER_COUNT = 4
QUERY_COUNT = 30
CALIBRATION_COUNT = 15
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
AnnotationLabel = Literal["relevant", "different", "uncertain"]
Split = Literal["calibration", "evaluation"]


@dataclass(frozen=True)
class BenchmarkFace:
    """One immutable source-run face, without an embedding."""

    face_id: str
    filename: str
    crop_path: str
    crop_sha256: str
    cluster_id: str
    status: str
    confidence: float
    sharpness: float
    relative_area: float

    def __post_init__(self) -> None:
        if (
            not self.face_id
            or not self.filename
            or not _is_relative_path(self.filename)
            or not self.cluster_id
            or not _is_relative_path(self.crop_path)
            or not _SHA256.fullmatch(self.crop_sha256)
            or not all(math.isfinite(value) for value in self.quality_values)
            or not 0 <= self.confidence <= 1
            or self.sharpness < 0
            or not 0 <= self.relative_area <= 1
        ):
            raise ValueError("invalid benchmark face")

    @property
    def quality_values(self) -> tuple[float, float, float]:
        return (self.confidence, self.sharpness, self.relative_area)


@dataclass(frozen=True)
class BenchmarkRun:
    """Frozen source-run values parsed by a caller before proposal construction."""

    manifest_sha256: str
    faces_sha256: str
    faces: tuple[BenchmarkFace, ...]

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.manifest_sha256) or not _SHA256.fullmatch(self.faces_sha256):
            raise ValueError("benchmark run hashes must be lowercase SHA-256")
        if not self.faces:
            raise ValueError("benchmark run has no faces")
        identifiers = [face.face_id for face in self.faces]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("benchmark run face IDs must be unique")

    @property
    def face_by_id(self) -> dict[str, BenchmarkFace]:
        return {face.face_id: face for face in self.faces}


@dataclass(frozen=True)
class BenchmarkSource:
    run_manifest_sha256: str
    faces_sha256: str
    index_manifest_sha256: str
    proposal_sha256: str

    def __post_init__(self) -> None:
        if not all(
            _SHA256.fullmatch(value)
            for value in (
                self.run_manifest_sha256,
                self.faces_sha256,
                self.index_manifest_sha256,
                self.proposal_sha256,
            )
        ):
            raise ValueError("benchmark source hashes must be lowercase SHA-256")


@dataclass(frozen=True)
class BenchmarkQuery:
    query_id: str
    query_face_id: str
    query_filename: str
    query_crop_path: str
    proposed_cluster_id: str
    candidate_face_ids: tuple[str, ...]
    split: Split


@dataclass(frozen=True)
class Annotation:
    query_id: str
    candidate_face_id: str
    label: str
    note: str | None = None


@dataclass(frozen=True)
class BenchmarkProposal:
    queries: tuple[BenchmarkQuery, ...]
    replacement_queries: tuple[BenchmarkQuery, ...]
    faces: tuple[BenchmarkFace, ...]
    source: BenchmarkSource

    def __post_init__(self) -> None:
        _validate_query_collection(self.all_queries, self.faces)
        _validate_proposal_digest(self)

    @property
    def all_queries(self) -> tuple[BenchmarkQuery, ...]:
        return self.queries + self.replacement_queries

    @property
    def face_by_id(self) -> dict[str, BenchmarkFace]:
        return {face.face_id: face for face in self.faces}


@dataclass(frozen=True)
class FinalBenchmark:
    queries: tuple[BenchmarkQuery, ...]
    annotations: tuple[Annotation, ...]
    faces: tuple[BenchmarkFace, ...]
    source: BenchmarkSource

    def __post_init__(self) -> None:
        if len(self.queries) != QUERY_COUNT:
            raise ValueError("final benchmark requires exactly 30 queries")
        _validate_query_collection(self.queries, self.faces)
        if sum(query.split == "calibration" for query in self.queries) != CALIBRATION_COUNT:
            raise ValueError("final benchmark requires 15 calibration queries")
        if sum(query.split == "evaluation" for query in self.queries) != CALIBRATION_COUNT:
            raise ValueError("final benchmark requires 15 evaluation queries")
        _validate_annotations_for_queries(self.queries, self.face_by_id, self.annotations)
        if self.annotations != tuple(sorted(self.annotations, key=_annotation_sort_key)):
            raise ValueError("final benchmark annotations must be ordered")
        annotations_by_query = {
            query.query_id: tuple(
                annotation
                for annotation in self.annotations
                if annotation.query_id == query.query_id
            )
            for query in self.queries
        }
        if any(
            not _query_is_valid(query, annotations_by_query[query.query_id], self.face_by_id)
            for query in self.queries
        ):
            raise ValueError("final benchmark contains an invalid query")
        if any(
            len(component) > 1
            for component in _manual_identity_components(self.queries, self.annotations).values()
        ):
            raise ValueError("final benchmark contains duplicate manual identities")

    @property
    def face_by_id(self) -> dict[str, BenchmarkFace]:
        return {face.face_id: face for face in self.faces}


def build_benchmark_proposal(
    run: BenchmarkRun, index: FaceIndex, query_count: int = QUERY_COUNT
) -> BenchmarkProposal:
    """Create a deterministic, review-only candidate bundle from an accepted index."""
    if not isinstance(run, BenchmarkRun):
        raise TypeError("run must be a BenchmarkRun")
    if not isinstance(index, FaceIndex):
        raise TypeError("index must be a FaceIndex")
    if isinstance(query_count, bool) or not isinstance(query_count, int) or query_count < 1:
        raise ValueError("query count must be positive")
    _validate_index_binding(run, index)
    eligible = _ordered_eligible_queries(run, index)
    if len(eligible) < query_count:
        raise ValueError("insufficient eligible benchmark people")
    primary = tuple(eligible[:query_count])
    primary = _assign_splits(primary, run.face_by_id, _accepted_cluster_sizes(run, index))
    replacements = tuple(replace(query, split="evaluation") for query in eligible[query_count:])
    source_without_proposal = {
        "run_manifest_sha256": run.manifest_sha256,
        "faces_sha256": run.faces_sha256,
        "index_manifest_sha256": _index_manifest_sha256(index),
    }
    digest = _proposal_digest(primary, replacements, run.faces, source_without_proposal)
    return BenchmarkProposal(
        primary,
        replacements,
        run.faces,
        BenchmarkSource(**source_without_proposal, proposal_sha256=digest),
    )


def finalize_benchmark(
    proposal: BenchmarkProposal, annotations: Sequence[Annotation]
) -> FinalBenchmark:
    """Use manually supplied labels only; source clusters never become relevance labels."""
    if not isinstance(proposal, BenchmarkProposal):
        raise TypeError("proposal must be a BenchmarkProposal")
    values = tuple(annotations)
    validate_annotations(proposal, values)
    by_query: dict[str, tuple[Annotation, ...]] = {
        query.query_id: tuple(item for item in values if item.query_id == query.query_id)
        for query in proposal.all_queries
    }
    components = _manual_identity_components(proposal.all_queries, values)
    replacements = deque(proposal.replacement_queries)
    selected: list[BenchmarkQuery] = []
    for query in proposal.queries:
        chosen = (
            query
            if _can_select_query(query, by_query, proposal.face_by_id, components, selected)
            else None
        )
        while chosen is None and replacements:
            replacement = replacements.popleft()
            if _can_select_query(replacement, by_query, proposal.face_by_id, components, selected):
                chosen = replace(replacement, split=query.split)
        if chosen is not None:
            selected.append(chosen)
    if len(selected) != QUERY_COUNT:
        raise ValueError("finalization requires 30 valid queries")
    selected_ids = {query.query_id for query in selected}
    final_annotations = tuple(
        sorted(
            (item for item in values if item.query_id in selected_ids),
            key=_annotation_sort_key,
        )
    )
    return FinalBenchmark(tuple(selected), final_annotations, proposal.faces, proposal.source)


def validate_annotations(proposal: BenchmarkProposal, annotations: Sequence[Annotation]) -> None:
    """Validate explicit labels without assigning a label to an unreviewed face."""
    _validate_annotations_for_queries(proposal.all_queries, proposal.face_by_id, annotations)


def _validate_annotations_for_queries(
    query_values: Sequence[BenchmarkQuery],
    faces: Mapping[str, BenchmarkFace],
    annotations: Sequence[Annotation],
) -> None:
    queries = {query.query_id: query for query in query_values}
    seen: set[tuple[str, str]] = set()
    for annotation in annotations:
        if not isinstance(annotation, Annotation):
            raise TypeError("annotations must be Annotation values")
        key = (annotation.query_id, annotation.candidate_face_id)
        if key in seen:
            raise ValueError("duplicate annotation")
        seen.add(key)
        if annotation.label not in {"relevant", "different", "uncertain"}:
            raise ValueError("invalid annotation label")
        if annotation.note is not None and ("\r" in annotation.note or "\n" in annotation.note):
            raise ValueError("annotation note must be single-line")
        query = queries.get(annotation.query_id)
        if query is None:
            raise ValueError("annotation query is unknown")
        face = faces.get(annotation.candidate_face_id)
        if face is None:
            raise ValueError("annotation candidate is unknown")
        if annotation.candidate_face_id not in query.candidate_face_ids:
            raise ValueError("annotation candidate does not belong to query")
        if face.filename == query.query_filename:
            raise ValueError("annotation leaks the held-out photo")


def _ordered_eligible_queries(run: BenchmarkRun, index: FaceIndex) -> tuple[BenchmarkQuery, ...]:
    index_by_id = {entry.face_id: entry for entry in index.entries}
    by_cluster: dict[str, list[BenchmarkFace]] = defaultdict(list)
    for face in run.faces:
        if face.status == "ok":
            by_cluster[face.cluster_id].append(face)
    strata: dict[tuple[int, int], list[BenchmarkQuery]] = defaultdict(list)
    for cluster_id, faces in by_cluster.items():
        accepted = [face for face in faces if face.face_id in index_by_id]
        if len({face.filename for face in accepted}) < 4:
            continue
        query_face = sorted(
            accepted,
            key=lambda face: (-face.confidence, -face.sharpness, -face.relative_area, face.face_id),
        )[0]
        query = BenchmarkQuery(
            query_id=f"query-{cluster_id}",
            query_face_id=query_face.face_id,
            query_filename=query_face.filename,
            query_crop_path=query_face.crop_path,
            proposed_cluster_id=cluster_id,
            candidate_face_ids=_candidate_pool(query_face, cluster_id, accepted, run, index),
            split="evaluation",
        )
        strata[(_cluster_size_band(len(accepted)), _quality_band(query_face))].append(query)
    queues = {
        key: deque(sorted(values, key=lambda query: query.query_id))
        for key, values in strata.items()
    }
    ordered: list[BenchmarkQuery] = []
    while queues:
        for key in sorted(tuple(queues)):
            queue = queues[key]
            ordered.append(queue.popleft())
            if not queue:
                del queues[key]
    return tuple(ordered)


def _candidate_pool(
    query_face: BenchmarkFace,
    cluster_id: str,
    cluster_faces: Sequence[BenchmarkFace],
    run: BenchmarkRun,
    index: FaceIndex,
) -> tuple[str, ...]:
    entry_positions = {entry.face_id: position for position, entry in enumerate(index.entries)}
    query_position = entry_positions[query_face.face_id]
    same_cluster = sorted(
        [
            face.face_id
            for face in cluster_faces
            if face.face_id != query_face.face_id and face.filename != query_face.filename
        ]
    )
    cross_cluster = sorted(
        [
            face.face_id
            for face in run.faces
            if face.status == "ok"
            and face.cluster_id != cluster_id
            and face.filename != query_face.filename
            and face.face_id in entry_positions
        ]
    )
    distances = {
        face_id: float(
            1.0
            - np.dot(index.embeddings[query_position], index.embeddings[entry_positions[face_id]])
        )
        for face_id in cross_cluster
    }
    nearest = sorted(cross_cluster, key=lambda face_id: (distances[face_id], face_id))[
        :NEAREST_CROSS_CLUSTER_COUNT
    ]
    nearest_ids = set(nearest)
    distant = sorted(
        (face_id for face_id in cross_cluster if face_id not in nearest_ids),
        key=lambda face_id: (-distances[face_id], face_id),
    )[:DISTANT_CROSS_CLUSTER_COUNT]
    return tuple(same_cluster + nearest + distant)


def _assign_splits(
    queries: tuple[BenchmarkQuery, ...],
    faces: Mapping[str, BenchmarkFace],
    accepted_cluster_sizes: Mapping[str, int],
) -> tuple[BenchmarkQuery, ...]:
    if len(queries) != QUERY_COUNT:
        return tuple(replace(query, split="evaluation") for query in queries)
    by_stratum: dict[tuple[int, int], deque[BenchmarkQuery]] = defaultdict(deque)
    for query in queries:
        cluster_size = accepted_cluster_sizes[query.proposed_cluster_id]
        by_stratum[
            (_cluster_size_band(cluster_size), _quality_band(faces[query.query_face_id]))
        ].append(query)
    normalized = {
        key: tuple(sorted(values, key=lambda query: query.query_id))
        for key, values in by_stratum.items()
    }
    calibration_ids = _stratified_calibration_ids(normalized)
    return tuple(
        replace(query, split="calibration" if query.query_id in calibration_ids else "evaluation")
        for query in queries
    )


def _stratified_calibration_ids(
    strata: Mapping[tuple[int, int], Sequence[BenchmarkQuery]],
) -> frozenset[str]:
    baseline = sum(len(queries) // 2 for queries in strata.values())
    extra_calibration = CALIBRATION_COUNT - baseline
    calibration_ids: set[str] = set()
    for key in sorted(strata):
        queries = strata[key]
        calibration_count = len(queries) // 2
        calibration_first = False
        if len(queries) % 2 and extra_calibration:
            calibration_count += 1
            calibration_first = True
            extra_calibration -= 1
        elif len(queries) % 2:
            calibration_first = False
        else:
            calibration_first = True
        positions = range(0 if calibration_first else 1, len(queries), 2)
        selected_positions = tuple(positions)[:calibration_count]
        calibration_ids.update(queries[position].query_id for position in selected_positions)
    if len(calibration_ids) != CALIBRATION_COUNT or extra_calibration:
        raise ValueError("split cannot satisfy calibration count")
    return frozenset(calibration_ids)


def _query_is_valid(
    query: BenchmarkQuery, annotations: Sequence[Annotation], faces: Mapping[str, BenchmarkFace]
) -> bool:
    relevant_filenames = {
        faces[annotation.candidate_face_id].filename
        for annotation in annotations
        if annotation.label == "relevant"
        and faces[annotation.candidate_face_id].filename != query.query_filename
    }
    return len(relevant_filenames) >= 3


def _can_select_query(
    query: BenchmarkQuery,
    annotations_by_query: Mapping[str, Sequence[Annotation]],
    faces: Mapping[str, BenchmarkFace],
    components: Mapping[str, frozenset[str]],
    selected: Sequence[BenchmarkQuery],
) -> bool:
    if not _query_is_valid(query, annotations_by_query[query.query_id], faces):
        return False
    component = components[query.query_id]
    return all(existing.query_id not in component for existing in selected)


def _manual_identity_components(
    queries: Sequence[BenchmarkQuery], annotations: Sequence[Annotation]
) -> dict[str, frozenset[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for query in queries:
        query_node = _query_node(query.query_id)
        face_node = _face_node(query.query_face_id)
        graph[query_node].add(face_node)
        graph[face_node].add(query_node)
    for annotation in annotations:
        if annotation.label != "relevant":
            continue
        query_node = _query_node(annotation.query_id)
        face_node = _face_node(annotation.candidate_face_id)
        graph[query_node].add(face_node)
        graph[face_node].add(query_node)
    result: dict[str, frozenset[str]] = {}
    visited: set[str] = set()
    for start in sorted(graph):
        if start in visited:
            continue
        pending = [start]
        component: set[str] = set()
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            pending.extend(sorted(graph[node] - visited, reverse=True))
        query_ids = frozenset(node[2:] for node in component if node.startswith("q:"))
        for query_id in query_ids:
            result[query_id] = query_ids
    return result


def _query_node(query_id: str) -> str:
    return f"q:{query_id}"


def _face_node(face_id: str) -> str:
    return f"f:{face_id}"


def _annotation_sort_key(annotation: Annotation) -> tuple[str, str, str, str]:
    return (
        annotation.query_id,
        annotation.candidate_face_id,
        annotation.label,
        annotation.note or "",
    )


def _validate_index_binding(run: BenchmarkRun, index: FaceIndex) -> None:
    if (
        index.manifest.source_run_manifest_sha256 != run.manifest_sha256
        or index.manifest.source_faces_sha256 != run.faces_sha256
    ):
        raise ValueError("index source identities do not match benchmark run")
    run_by_id = run.face_by_id
    indexed_ids = {entry.face_id for entry in index.entries}
    expected_ids = {face.face_id for face in run.faces if face.status == "ok"}
    if indexed_ids != expected_ids:
        raise ValueError("index entries do not reconcile with accepted run faces")
    for entry in index.entries:
        source = run_by_id[entry.face_id]
        if entry.filename != source.filename or entry.crop_path != source.crop_path:
            raise ValueError("index face identity does not match benchmark run")


def _accepted_cluster_sizes(run: BenchmarkRun, index: FaceIndex) -> dict[str, int]:
    indexed_ids = {entry.face_id for entry in index.entries}
    counts: dict[str, int] = defaultdict(int)
    for face in run.faces:
        if face.status == "ok" and face.face_id in indexed_ids:
            counts[face.cluster_id] += 1
    return dict(counts)


def _validate_query_collection(
    queries: Sequence[BenchmarkQuery], faces: Sequence[BenchmarkFace]
) -> None:
    face_by_id = {face.face_id: face for face in faces}
    query_ids = [query.query_id for query in queries]
    cluster_ids = [query.proposed_cluster_id for query in queries]
    if len(query_ids) != len(set(query_ids)) or len(cluster_ids) != len(set(cluster_ids)):
        raise ValueError("benchmark queries must be unique people")
    for query in queries:
        source = face_by_id.get(query.query_face_id)
        if (
            source is None
            or query.query_filename != source.filename
            or query.query_crop_path != source.crop_path
            or query.proposed_cluster_id != source.cluster_id
            or not query.query_id
            or query.split not in {"calibration", "evaluation"}
            or len(query.candidate_face_ids) != len(set(query.candidate_face_ids))
        ):
            raise ValueError("invalid benchmark query")
        for candidate_id in query.candidate_face_ids:
            candidate = face_by_id.get(candidate_id)
            if candidate is None or candidate.filename == query.query_filename:
                raise ValueError("benchmark query leaks held-out photo")


def _validate_proposal_digest(proposal: BenchmarkProposal) -> None:
    expected = _proposal_digest(
        proposal.queries,
        proposal.replacement_queries,
        proposal.faces,
        {
            "run_manifest_sha256": proposal.source.run_manifest_sha256,
            "faces_sha256": proposal.source.faces_sha256,
            "index_manifest_sha256": proposal.source.index_manifest_sha256,
        },
    )
    if proposal.source.proposal_sha256 != expected:
        raise ValueError("proposal source hash does not match contents")


def _cluster_size_band(size: int) -> int:
    return 0 if size <= 5 else 1 if size <= 9 else 2


def _quality_band(face: BenchmarkFace) -> int:
    if face.confidence >= 0.94 and face.sharpness >= 100:
        return 0
    if face.confidence >= 0.88 and face.sharpness >= 60:
        return 1
    return 2


def _index_manifest_sha256(index: FaceIndex) -> str:
    return _sha256_json(index.manifest.to_dict())


def _proposal_digest(
    queries: Sequence[BenchmarkQuery],
    replacements: Sequence[BenchmarkQuery],
    faces: Sequence[BenchmarkFace],
    source: Mapping[str, str],
) -> str:
    return _sha256_json(
        {
            "schema_version": SCHEMA_VERSION,
            "queries": [_query_dict(query) for query in queries],
            "replacement_queries": [_query_dict(query) for query in replacements],
            "faces": [_face_dict(face) for face in faces],
            "source": dict(source),
        }
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _face_dict(face: BenchmarkFace) -> dict[str, object]:
    return {
        "face_id": face.face_id,
        "filename": face.filename,
        "crop_path": face.crop_path,
        "cluster_id": face.cluster_id,
        "status": face.status,
        "confidence": face.confidence,
        "sharpness": face.sharpness,
        "relative_area": face.relative_area,
    }


def _query_dict(query: BenchmarkQuery) -> dict[str, object]:
    return {
        "query_id": query.query_id,
        "query_face_id": query.query_face_id,
        "query_filename": query.query_filename,
        "query_crop_path": query.query_crop_path,
        "proposed_cluster_id": query.proposed_cluster_id,
        "candidate_face_ids": list(query.candidate_face_ids),
        "split": query.split,
    }


def _is_relative_path(value: str) -> bool:
    path = Path(value)
    windows = PureWindowsPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and "\\" not in value
        and "\x00" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )
