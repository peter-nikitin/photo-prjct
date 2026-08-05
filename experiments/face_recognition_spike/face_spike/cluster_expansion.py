"""Private, aggregate-only evaluation of direct-first face-cluster expansion."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tracemalloc
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TypedDict, cast

import numpy as np
from face_cluster_contract import (
    ClusterFaceValue,
    build_face_cluster_kernel,
    corpus_configuration_hash,
)

from .benchmark import BenchmarkQuery, FinalBenchmark
from .index import FaceIndex

_SHA256_LENGTH = 64


class _HeldOutParameters(TypedDict):
    edge_threshold: float
    representative_threshold: float
    distance_block_size: int
    max_candidate_edges: int


@dataclass(frozen=True)
class ClusterMember:
    face_id: str
    filename: str
    distance_to_representative: float


@dataclass(frozen=True)
class FaceCluster:
    cluster_id: str
    representative_face_id: str
    members: tuple[ClusterMember, ...]


@dataclass(frozen=True)
class ClusterExpansionConfiguration:
    direct_threshold: float
    anchor_threshold: float
    configuration_hash: str

    def __post_init__(self) -> None:
        if (
            not _valid_threshold(self.direct_threshold)
            or not _valid_threshold(self.anchor_threshold)
            or self.anchor_threshold >= self.direct_threshold
            or not _is_sha256(self.configuration_hash)
        ):
            raise ValueError("invalid cluster-expansion configuration")


@dataclass(frozen=True)
class RankedPhoto:
    filename: str
    face_id: str
    cosine_distance: float | None
    source: str


@dataclass(frozen=True)
class RankedSearch:
    photos: tuple[RankedPhoto, ...]
    direct_photo_count: int
    expanded_photo_count: int
    final_photo_count: int
    strong_anchor_count: int
    selected_cluster_count: int
    selected_cluster_ids: tuple[str, ...]
    direct_ranking_ms: int
    expansion_ms: int


@dataclass(frozen=True)
class ClusterExpansionEvaluationReport:
    corpus_configuration_hash: str
    evaluation_configuration_hash: str
    direct_threshold: float
    anchor_threshold: float
    source: Mapping[str, str]
    splits: Mapping[str, Mapping[str, object]]
    cluster_metrics: Mapping[str, object]
    resources: Mapping[str, object]
    resource_evidence: Mapping[str, object]
    report_sha256: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "corpus_configuration_hash": self.corpus_configuration_hash,
            "evaluation_configuration_hash": self.evaluation_configuration_hash,
            "thresholds": {
                "direct": self.direct_threshold,
                "anchor": self.anchor_threshold,
            },
            "source": dict(self.source),
            "splits": {name: dict(value) for name, value in self.splits.items()},
            "cluster_metrics": dict(self.cluster_metrics),
            "resource_evidence": dict(self.resource_evidence),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.canonical_payload())

    def payload(self) -> dict[str, object]:
        return {
            **self.canonical_payload(),
            "resources": dict(self.resources),
            "report_sha256": self.report_sha256,
        }

    def to_json(self) -> str:
        return (
            json.dumps(self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        )


def build_held_out_clusters(
    index: FaceIndex,
    *,
    query_filename: str,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
) -> tuple[FaceCluster, ...]:
    """Rebuild the exact graph corpus after removing a query's entire source photo.

    The evaluator deliberately does not filter a pre-built cluster after the fact: a held-out face
    can alter an accepted merge or a component medoid.  This is the same guarded graph policy used
    by the production corpus builder, applied to the exact normalized embeddings being evaluated.
    """
    if not _valid_threshold(edge_threshold) or not _valid_threshold(representative_threshold):
        raise ValueError("invalid cluster thresholds")
    if (
        isinstance(distance_block_size, bool)
        or not isinstance(distance_block_size, int)
        or distance_block_size < 1
        or isinstance(max_candidate_edges, bool)
        or not isinstance(max_candidate_edges, int)
        or max_candidate_edges < 1
    ):
        raise ValueError("invalid clustering limits")
    return tuple(
        FaceCluster(
            cluster.cluster_key,
            cluster.representative_face_id,
            tuple(
                ClusterMember(
                    member.face_id,
                    next(
                        entry.filename for entry in index.entries if entry.face_id == member.face_id
                    ),
                    member.distance_to_representative,
                )
                for member in cluster.members
            ),
        )
        for cluster in build_face_cluster_kernel(
            tuple(
                ClusterFaceValue(
                    entry.face_id, tuple(float(value) for value in index.embeddings[position])
                )
                for position, entry in enumerate(index.entries)
                if entry.filename != query_filename
            ),
            edge_threshold=edge_threshold,
            representative_threshold=representative_threshold,
            distance_block_size=distance_block_size,
            max_candidate_edges=max_candidate_edges,
        )
    )


def production_corpus_configuration_hash(
    index: FaceIndex,
    *,
    source_parameters: Mapping[str, object],
    generations: Sequence[Mapping[str, object]],
) -> str:
    """Project a strictly validated run onto the production corpus-hash contract.

    This function has no Django dependency. It uses the actual normalized production generation
    dictionaries plus the algorithm thresholds and bounded-build limits, exactly as Django does.
    """
    parameters = _held_out_parameters(source_parameters)
    return corpus_configuration_hash(
        algorithm_version="guarded-graph-v1",
        generations=generations,
        dimensions=index.manifest.embedding_dimension,
        edge_threshold=parameters["edge_threshold"],
        representative_threshold=parameters["representative_threshold"],
        distance_block_size=parameters["distance_block_size"],
        max_candidate_edges=parameters["max_candidate_edges"],
    )


def rank_cluster_expansion(
    query: object,
    benchmark: FinalBenchmark,
    index: FaceIndex,
    clusters: Sequence[FaceCluster],
    configuration: ClusterExpansionConfiguration,
) -> RankedSearch:
    """Apply the production direct-first, strict-anchor policy to one held-out query."""
    if not isinstance(benchmark, FinalBenchmark) or not isinstance(index, FaceIndex):
        raise TypeError("benchmark and index must be immutable artifacts")
    _validate_artifact_binding(benchmark, index)
    if query not in benchmark.queries:
        raise ValueError("query is not in the final benchmark")
    if not isinstance(configuration, ClusterExpansionConfiguration):
        raise TypeError("configuration must be a ClusterExpansionConfiguration")
    memberships = _membership_by_face(clusters, index, require_complete=False)
    positions = {entry.face_id: position for position, entry in enumerate(index.entries)}
    query_position = positions.get(query.query_face_id)
    if query_position is None:
        raise ValueError("query is missing from index")
    query_vector = index.embeddings[query_position]
    direct_started = perf_counter()
    direct_by_photo: dict[str, RankedPhoto] = {}
    for position, entry in enumerate(index.entries):
        if entry.filename == query.query_filename:
            continue
        distance = float(np.clip(1.0 - np.dot(query_vector, index.embeddings[position]), 0.0, 2.0))
        if distance > configuration.direct_threshold:
            continue
        candidate = RankedPhoto(entry.filename, entry.face_id, distance, "direct")
        previous = direct_by_photo.get(entry.filename)
        if previous is None or (candidate.cosine_distance, candidate.face_id) < (
            previous.cosine_distance,
            previous.face_id,
        ):
            direct_by_photo[entry.filename] = candidate
    direct = tuple(
        sorted(direct_by_photo.values(), key=lambda row: (row.cosine_distance, row.filename))
    )
    direct_ranking_ms = max(0, round((perf_counter() - direct_started) * 1_000))
    expansion_started = perf_counter()
    anchors = tuple(
        row
        for row in direct
        if row.cosine_distance is not None
        and row.cosine_distance <= configuration.anchor_threshold
        and row.face_id in memberships
    )
    first_anchor: dict[str, RankedPhoto] = {}
    for anchor in anchors:
        first_anchor.setdefault(memberships[anchor.face_id].cluster_id, anchor)
    selected = tuple(
        sorted(
            first_anchor.items(),
            key=lambda item: (
                next(position for position, row in enumerate(direct) if row is item[1]),
                item[1].cosine_distance,
                item[1].filename,
                item[0],
            ),
        )
    )
    seen = {row.filename for row in direct}
    appended: list[RankedPhoto] = []
    for _cluster_id, _anchor in selected:
        members = memberships[_anchor.face_id].members
        best_by_photo: dict[str, ClusterMember] = {}
        for member in members:
            if member.filename != query.query_filename:
                previous_member = best_by_photo.get(member.filename)
                if previous_member is None or (
                    member.distance_to_representative,
                    member.face_id,
                ) < (
                    previous_member.distance_to_representative,
                    previous_member.face_id,
                ):
                    best_by_photo[member.filename] = member
        for filename, member in sorted(
            best_by_photo.items(),
            key=lambda item: (item[1].distance_to_representative, item[0], item[1].face_id),
        ):
            if filename not in seen:
                seen.add(filename)
                appended.append(
                    RankedPhoto(filename, member.face_id, None, "face_cluster_expansion")
                )
    photos = (*direct, *appended)
    return RankedSearch(
        photos=photos,
        direct_photo_count=len(direct),
        expanded_photo_count=len(appended),
        final_photo_count=len(photos),
        strong_anchor_count=len(anchors),
        selected_cluster_count=sum(
            len(memberships[anchor.face_id].members) > 1 for _cluster_id, anchor in selected
        ),
        selected_cluster_ids=tuple(cluster_id for cluster_id, _anchor in selected),
        direct_ranking_ms=direct_ranking_ms,
        expansion_ms=max(0, round((perf_counter() - expansion_started) * 1_000)),
    )


def evaluate_cluster_expansion(
    benchmark: FinalBenchmark,
    index: FaceIndex,
    clusters: Sequence[FaceCluster],
    configuration: ClusterExpansionConfiguration,
    *,
    corpus_build_duration_ms: int | None = None,
    corpus_build_peak_memory_bytes: int | None = None,
    cluster_parameters: Mapping[str, object] | None = None,
    source_identities: Mapping[str, str] | None = None,
) -> ClusterExpansionEvaluationReport:
    """Evaluate only the labelled final benchmark and retain aggregate evidence only."""
    _validate_artifact_binding(benchmark, index)
    cluster_values = tuple(clusters)
    _membership_by_face(cluster_values, index)
    tracing_was_active = tracemalloc.is_tracing()
    if not tracing_was_active:
        tracemalloc.start()
    direct_times: dict[str, list[float]] = defaultdict(list)
    expansion_times: dict[str, list[float]] = defaultdict(list)
    by_split: dict[str, list[tuple[BenchmarkQuery, RankedSearch, tuple[FaceCluster, ...]]]] = {
        "calibration": [],
        "evaluation": [],
    }
    held_out_parameters = _held_out_parameters(cluster_parameters)
    for query in benchmark.queries:
        held_out_clusters = build_held_out_clusters(
            index,
            query_filename=query.query_filename,
            edge_threshold=held_out_parameters["edge_threshold"],
            representative_threshold=held_out_parameters["representative_threshold"],
            distance_block_size=held_out_parameters["distance_block_size"],
            max_candidate_edges=held_out_parameters["max_candidate_edges"],
        )
        result = rank_cluster_expansion(query, benchmark, index, held_out_clusters, configuration)
        direct_times[query.split].append(result.direct_ranking_ms)
        expansion_times[query.split].append(result.expansion_ms)
        by_split[query.split].append((query, result, held_out_clusters))
    _current, peak_memory_bytes = tracemalloc.get_traced_memory()
    splits = {split: _split_metrics(benchmark, results) for split, results in by_split.items()}
    source = dict(
        source_identities
        or stable_evaluation_source(benchmark, index, cluster_values, cluster_parameters or {})
    )
    cluster_metrics = {
        "global_intrinsic": _global_intrinsic_cluster_metrics(cluster_values),
        "held_out": {
            split: _held_out_cluster_metrics(benchmark, values)
            for split, values in by_split.items()
        },
    }
    resources: dict[str, object] = {
        "corpus_build_duration_ms": corpus_build_duration_ms,
        "corpus_build_peak_memory_bytes": corpus_build_peak_memory_bytes,
        "search_peak_memory_bytes": peak_memory_bytes,
        "direct_ranking_ms": {
            split: _percentiles(values) for split, values in direct_times.items()
        },
        "cluster_expansion_ms": {
            split: _percentiles(values) for split, values in expansion_times.items()
        },
    }
    evaluated_configuration = {
        "corpus_configuration_hash": configuration.configuration_hash,
        "direct_threshold": configuration.direct_threshold,
        "anchor_threshold": configuration.anchor_threshold,
        "source": source,
        "cluster_membership": _cluster_binding(cluster_values),
        "cluster_parameters": dict(cluster_parameters or {}),
    }
    evaluation_configuration_hash = hashlib.sha256(
        _canonical_json(evaluated_configuration)
    ).hexdigest()
    resource_evidence = {
        "corpus_build": {
            "identity": "cluster-run-manifest-v1",
            "measured": (
                corpus_build_duration_ms is not None and corpus_build_peak_memory_bytes is not None
            ),
        }
    }
    canonical = {
        "schema_version": 1,
        "corpus_configuration_hash": configuration.configuration_hash,
        "evaluation_configuration_hash": evaluation_configuration_hash,
        "thresholds": {
            "direct": configuration.direct_threshold,
            "anchor": configuration.anchor_threshold,
        },
        "source": source,
        "splits": splits,
        "cluster_metrics": cluster_metrics,
        "resource_evidence": resource_evidence,
    }
    report_sha256 = hashlib.sha256(_canonical_json(canonical)).hexdigest()
    if not tracing_was_active:
        tracemalloc.stop()
    return ClusterExpansionEvaluationReport(
        configuration.configuration_hash,
        evaluation_configuration_hash,
        configuration.direct_threshold,
        configuration.anchor_threshold,
        source,
        splits,
        cluster_metrics,
        resources,
        resource_evidence,
        report_sha256,
    )


def stable_evaluation_source(
    benchmark: FinalBenchmark,
    index: FaceIndex,
    clusters: Sequence[FaceCluster],
    cluster_parameters: Mapping[str, object],
    *,
    corpus_evidence_sha256: str | None = None,
) -> dict[str, str]:
    """Return report-safe identities without volatile artifact-manifest metadata."""
    benchmark_content = {
        "queries": [query.__dict__ for query in benchmark.queries],
        "annotations": [annotation.__dict__ for annotation in benchmark.annotations],
        "faces": [face.__dict__ for face in benchmark.faces],
    }
    index_content = {
        "entry_count": index.manifest.entry_count,
        "embedding_dimension": index.manifest.embedding_dimension,
        "yunet_model": index.manifest.yunet_model,
        "sface_model": index.manifest.sface_model,
        "parameters": index.manifest.parameters,
        "dependency_versions": index.manifest.dependency_versions,
        "entries": [
            {
                "face_id": entry.face_id,
                "filename": entry.filename,
                "face_index": entry.face_index,
                "crop_path": entry.crop_path,
                "bounding_box": entry.bounding_box.__dict__,
                "quality": entry.quality.__dict__,
            }
            for entry in index.entries
        ],
        "embedding_matrix_sha256": _embedding_matrix_sha256(index.embeddings),
    }
    corpus_content = {
        "parameters": dict(cluster_parameters),
        "membership": _cluster_binding(clusters),
    }
    return {
        "benchmark_content_sha256": hashlib.sha256(_canonical_json(benchmark_content)).hexdigest(),
        "corpus_evidence_sha256": corpus_evidence_sha256
        or hashlib.sha256(_canonical_json(corpus_content)).hexdigest(),
        "index_content_sha256": hashlib.sha256(_canonical_json(index_content)).hexdigest(),
    }


def _embedding_matrix_sha256(embeddings: np.ndarray) -> str:
    """Digest exact normalized float32 rows with an unambiguous binary layout contract."""
    matrix = np.ascontiguousarray(embeddings, dtype=np.float32)
    layout = _canonical_json(
        {
            "dtype": matrix.dtype.str,
            "order": "C",
            "shape": list(matrix.shape),
        }
    )
    return hashlib.sha256(layout + b"\0" + matrix.tobytes(order="C")).hexdigest()


def write_evaluation_report(path: Path, report: ClusterExpansionEvaluationReport) -> None:
    """Publish one aggregate JSON report without query, photo, face, or vector details."""
    if os.path.lexists(path):
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(report.to_json(), encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _split_metrics(
    benchmark: FinalBenchmark,
    values: Sequence[tuple[BenchmarkQuery, RankedSearch, Sequence[FaceCluster]]],
) -> dict[str, object]:
    annotations_by_photo: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    annotations_by_member: defaultdict[str, dict[str, str]] = defaultdict(dict)
    faces = benchmark.face_by_id
    for annotation in benchmark.annotations:
        filename = faces[annotation.candidate_face_id].filename
        annotations_by_photo[annotation.query_id][filename].add(annotation.label)
        annotations_by_member[annotation.query_id][annotation.candidate_face_id] = annotation.label
    direct_total = final_total = expanded_total = 0
    direct_correct = final_correct = 0
    expanded_correct = expanded_incorrect = 0
    direct_labels: Counter[str] = Counter()
    expanded_labels: Counter[str] = Counter()
    helped = harmed = false_merges = 0
    for query, result, clusters in values:
        labels = {
            filename: _photo_label(labels)
            for filename, labels in annotations_by_photo[query.query_id].items()
        }
        relevant = {filename for filename, label in labels.items() if label == "relevant"}
        direct = {row.filename for row in result.photos if row.source == "direct"}
        expanded = {row.filename for row in result.photos if row.source == "face_cluster_expansion"}
        final = direct | expanded
        direct_total += len(direct)
        expanded_total += len(expanded)
        final_total += len(final)
        direct_correct += len(direct & relevant)
        final_correct += len(final & relevant)
        if len(final & relevant) > len(direct & relevant):
            helped += 1
        for filename in direct:
            if labels.get(filename) in {"relevant", "different"}:
                direct_labels[labels[filename]] += 1
        for filename in expanded:
            label = labels.get(filename)
            if label in {"relevant", "different"}:
                expanded_labels[label] += 1
                if label == "relevant":
                    expanded_correct += 1
                else:
                    expanded_incorrect += 1
        if expanded & {filename for filename, label in labels.items() if label == "different"}:
            harmed += 1
        by_cluster = {cluster.cluster_id: cluster.members for cluster in clusters}
        selected_memberships = (
            by_cluster[cluster_id]
            for cluster_id in result.selected_cluster_ids
            if cluster_id in by_cluster
        )
        false_merges += sum(
            len(members) > 1
            and _is_false_cluster_merge(members, annotations_by_member[query.query_id])
            for members in selected_memberships
        )
    relevant_total = sum(
        [
            len(
                {
                    filename
                    for filename, labels in annotations_by_photo[query.query_id].items()
                    if _photo_label(labels) == "relevant"
                }
            )
            for query, _result, _clusters in values
        ]
    )
    return {
        "search_count": len(values),
        "counts": {
            "direct_photos": direct_total,
            "expanded_photos": expanded_total,
            "final_photos": final_total,
            "relevant_photos": relevant_total,
        },
        "recall": {
            "direct": _ratio(direct_correct, relevant_total),
            "final": _ratio(final_correct, relevant_total),
        },
        "precision": {
            "direct": _precision(direct_labels),
            "face_cluster_expansion": _precision(expanded_labels),
        },
        "incremental": {
            "correct_photos": expanded_correct,
            "incorrect_photos": expanded_incorrect,
        },
        "searches": {"helped": helped, "harmed": harmed},
        "false_cluster_merges": false_merges,
    }


def _global_intrinsic_cluster_metrics(clusters: Sequence[FaceCluster]) -> dict[str, object]:
    sizes = Counter(len(cluster.members) for cluster in clusters)
    return {
        "cluster_count": len(clusters),
        "singleton_count": sizes[1],
        "size_distribution": {str(size): count for size, count in sorted(sizes.items())},
    }


def _held_out_cluster_metrics(
    benchmark: FinalBenchmark,
    values: Sequence[tuple[BenchmarkQuery, RankedSearch, Sequence[FaceCluster]]],
) -> dict[str, object]:
    sizes: Counter[int] = Counter()
    singleton_count = cluster_count = 0
    fragments: Counter[int] = Counter()
    annotations_by_query: defaultdict[str, set[str]] = defaultdict(set)
    for annotation in benchmark.annotations:
        if annotation.label == "relevant":
            annotations_by_query[annotation.query_id].add(annotation.candidate_face_id)
    for query, _result, clusters in values:
        memberships = _membership_by_face(clusters, None)
        cluster_count += len(clusters)
        sizes.update(len(cluster.members) for cluster in clusters)
        singleton_count += sum(len(cluster.members) == 1 for cluster in clusters)
        relevant_clusters = {
            memberships[face_id].cluster_id
            for face_id in annotations_by_query[query.query_id]
            if face_id in memberships
        }
        if relevant_clusters:
            fragments[len(relevant_clusters)] += 1
    return {
        "search_count": len(values),
        "cluster_count": cluster_count,
        "singleton_count": singleton_count,
        "size_distribution": {str(size): count for size, count in sorted(sizes.items())},
        "fragmentation": {str(parts): count for parts, count in sorted(fragments.items())},
    }


def _photo_label(labels: set[str]) -> str:
    if "relevant" in labels:
        return "relevant"
    if "uncertain" in labels:
        return "uncertain"
    return "different"


def _is_false_cluster_merge(
    members: Sequence[ClusterMember], labels_by_face_id: Mapping[str, str]
) -> bool:
    """A selected non-singleton is mixed only by labelled face instances."""
    return (
        len(members) > 1
        and any(labels_by_face_id.get(member.face_id) == "relevant" for member in members)
        and any(labels_by_face_id.get(member.face_id) == "different" for member in members)
    )


def _held_out_parameters(cluster_parameters: Mapping[str, object] | None) -> _HeldOutParameters:
    if cluster_parameters is None:
        raise ValueError("explicit clustering parameters are required")
    parameters = cluster_parameters
    edge = parameters.get("cluster_threshold")
    representative = parameters.get("representative_threshold")
    block_size = parameters.get("distance_block_size")
    edge_limit = parameters.get("max_candidate_edges")
    if (
        not _valid_threshold(edge)
        or not _valid_threshold(representative)
        or isinstance(block_size, bool)
        or not isinstance(block_size, int)
        or block_size < 1
        or isinstance(edge_limit, bool)
        or not isinstance(edge_limit, int)
        or edge_limit < 1
    ):
        raise ValueError("invalid clustering parameters")
    return {
        "edge_threshold": float(cast(int | float, edge)),
        "representative_threshold": float(cast(int | float, representative)),
        "distance_block_size": block_size,
        "max_candidate_edges": edge_limit,
    }


def _cluster_binding(clusters: Sequence[FaceCluster]) -> list[dict[str, object]]:
    return [
        {
            "cluster_id": cluster.cluster_id,
            "representative_face_id": cluster.representative_face_id,
            "members": [
                {
                    "face_id": member.face_id,
                    "filename": member.filename,
                    "distance_to_representative": member.distance_to_representative,
                }
                for member in cluster.members
            ],
        }
        for cluster in clusters
    ]


@dataclass(frozen=True)
class _ClusterMembership:
    cluster_id: str
    members: tuple[ClusterMember, ...]


def _membership_by_face(
    clusters: Sequence[FaceCluster], index: FaceIndex | None, *, require_complete: bool = True
) -> dict[str, _ClusterMembership]:
    membership: dict[str, _ClusterMembership] = {}
    index_by_id = {entry.face_id: entry for entry in index.entries} if index is not None else None
    for cluster in clusters:
        if not cluster.cluster_id or not cluster.members:
            raise ValueError("cluster artifact is invalid")
        member_ids = [member.face_id for member in cluster.members]
        if cluster.representative_face_id not in member_ids or len(member_ids) != len(
            set(member_ids)
        ):
            raise ValueError("cluster artifact is invalid")
        for member in cluster.members:
            if (
                not member.filename
                or not _valid_threshold(member.distance_to_representative)
                or member.face_id in membership
                or (index_by_id is not None and member.face_id not in index_by_id)
                or (
                    index_by_id is not None
                    and index_by_id[member.face_id].filename != member.filename
                )
            ):
                raise ValueError("cluster artifact is invalid")
            membership[member.face_id] = _ClusterMembership(cluster.cluster_id, cluster.members)
    if index_by_id is not None and require_complete and set(membership) != set(index_by_id):
        raise ValueError("cluster artifact is incompatible with index")
    return membership


def _validate_artifact_binding(benchmark: FinalBenchmark, index: FaceIndex) -> None:
    manifest = index.manifest
    manifest_hash = hashlib.sha256(_canonical_json(manifest.to_dict())).hexdigest()
    if (
        benchmark.source.run_manifest_sha256 != manifest.source_run_manifest_sha256
        or benchmark.source.faces_sha256 != manifest.source_faces_sha256
        or benchmark.source.index_manifest_sha256 != manifest_hash
    ):
        raise ValueError("benchmark and index are incompatible")


def _precision(labels: Counter[str]) -> dict[str, int | None]:
    labelled = labels["relevant"] + labels["different"]
    return {
        "correct_photos": labels["relevant"],
        "incorrect_photos": labels["different"],
        "labelled_photos": labelled,
        "numerator": labels["relevant"],
        "denominator": labelled,
    }


def _ratio(numerator: int, denominator: int) -> dict[str, int | None]:
    return {"numerator": numerator, "denominator": denominator}


def _percentiles(values: Sequence[float]) -> dict[str, int]:
    if not values:
        return {"p50": 0, "p95": 0}
    ordered = sorted(values)
    return {
        "p50": max(0, round(float(np.percentile(ordered, 50)))),
        "p95": max(0, round(float(np.percentile(ordered, 95)))),
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _valid_threshold(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 2
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )
