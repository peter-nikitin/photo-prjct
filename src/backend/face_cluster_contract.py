"""Dependency-neutral identities shared by corpus builders and offline evaluation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isfinite, sqrt

import numpy as np
from numpy.typing import NDArray

ALGORITHM_VERSION = "guarded-graph-v1"
POLICY_ID = "face-cluster-expansion-policy-v1"
_NORMALIZATION_TOLERANCE = 1e-6
_MAX_COSINE_DISTANCE = 2.0


class CandidateEdgeLimitExceeded(RuntimeError):
    """The exact candidate graph exceeded its configured bounded edge count."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"candidate edge limit exceeded: {limit}")


@dataclass(frozen=True, slots=True)
class ClusterFaceValue:
    """One normalized embedding accepted by the pure deterministic kernel."""

    face_id: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, str) or not self.face_id:
            raise ValueError("face_id must not be empty")
        if not isinstance(self.vector, tuple):
            raise ValueError("vector must be a tuple")
        _validate_vector(self.vector)


@dataclass(frozen=True, slots=True)
class ClusterMemberValue:
    face_id: str
    distance_to_representative: float


@dataclass(frozen=True, slots=True)
class BuiltClusterValue:
    cluster_key: str
    representative_face_id: str
    members: tuple[ClusterMemberValue, ...]


def canonical_json_hash(value: object) -> str:
    """Return the stable SHA-256 identity for a JSON-compatible contract value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def corpus_configuration(
    *,
    algorithm_version: str,
    generations: Sequence[Mapping[str, object]],
    dimensions: int,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
) -> dict[str, object]:
    """Build the immutable algorithm/configuration identity, excluding corpus contents."""
    return {
        "algorithm_version": algorithm_version,
        "embedding_dimensions": dimensions,
        "face_embedding_generations": _normalized_generations(generations),
        "edge_threshold": edge_threshold,
        "representative_threshold": representative_threshold,
        "distance_block_size": distance_block_size,
        "max_candidate_edges": max_candidate_edges,
    }


def corpus_configuration_hash(**kwargs: object) -> str:
    return canonical_json_hash(corpus_configuration(**kwargs))  # type: ignore[arg-type]


def cluster_expansion_policy_hash(
    corpus_configuration_hash: str,
    direct_threshold: float,
    anchor_threshold: float,
) -> str:
    """Bind reviewed expansion thresholds to one immutable corpus identity."""
    if not is_sha256(corpus_configuration_hash):
        raise ValueError("invalid corpus configuration hash")
    return canonical_json_hash(
        {
            "policy_id": POLICY_ID,
            "corpus_configuration_hash": corpus_configuration_hash,
            "direct_threshold": direct_threshold,
            "anchor_threshold": anchor_threshold,
        }
    )


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalized_generations(generations: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    if not generations:
        raise ValueError("face-embedding generations are required")
    normalized: list[dict[str, object]] = []
    required = {
        "contract_version",
        "processor_type",
        "processor_version",
        "configuration",
        "configuration_hash",
        "model",
    }
    for generation in generations:
        value = dict(generation)
        if set(value) != required:
            raise ValueError("invalid face-embedding generation")
        if (
            isinstance(value["contract_version"], bool)
            or not isinstance(value["contract_version"], int)
            or value["contract_version"] < 1
            or value["processor_type"] != "face_embedding"
            or isinstance(value["processor_version"], bool)
            or not isinstance(value["processor_version"], int)
            or value["processor_version"] < 1
            or not isinstance(value["configuration"], Mapping)
            or not is_sha256(value["configuration_hash"])
            or not isinstance(value["model"], str)
            or not value["model"]
        ):
            raise ValueError("invalid face-embedding generation")
        normalized.append(json.loads(canonical_json_bytes(value).decode()))
    return normalized


def build_face_cluster_kernel(
    faces: Sequence[ClusterFaceValue],
    *,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
) -> tuple[BuiltClusterValue, ...]:
    """Build exact deterministic guarded graph clusters without Django dependencies."""
    _validate_threshold(edge_threshold, "edge_threshold")
    _validate_threshold(representative_threshold, "representative_threshold")
    _validate_positive_integer(distance_block_size, "distance_block_size")
    _validate_positive_integer(max_candidate_edges, "max_candidate_edges")
    ordered = tuple(sorted(faces, key=lambda face: face.face_id))
    ids = tuple(face.face_id for face in ordered)
    if len(set(ids)) != len(ids):
        raise ValueError("face IDs must be unique")
    if not ordered:
        return ()
    vectors = _validated_matrix(ordered)
    parent = list(range(len(ordered)))
    members: dict[int, tuple[int, ...]] = {index: (index,) for index in range(len(ordered))}
    representatives = {index: index for index in range(len(ordered))}
    for _distance, left, right in _candidate_edges(
        ids, vectors, edge_threshold, distance_block_size, max_candidate_edges
    ):
        left_root = _find(parent, left)
        right_root = _find(parent, right)
        if left_root == right_root:
            continue
        proposed = tuple(sorted((*members[left_root], *members[right_root])))
        representative = _component_medoid(proposed, ids, vectors, distance_block_size)
        if not _within_representative_radius(
            proposed, representative, vectors, representative_threshold, distance_block_size
        ):
            continue
        new_root, absorbed_root = sorted((left_root, right_root))
        parent[absorbed_root] = new_root
        members[new_root] = proposed
        representatives[new_root] = representative
        del members[absorbed_root]
        del representatives[absorbed_root]
    components = sorted(
        members.values(), key=lambda component: tuple(ids[index] for index in component)
    )
    return tuple(
        BuiltClusterValue(
            cluster_key=f"cluster-{number:04d}",
            representative_face_id=ids[representatives[_find(parent, component[0])]],
            members=tuple(
                ClusterMemberValue(
                    face_id=ids[index],
                    distance_to_representative=_cosine_distance(
                        vectors[index], vectors[representatives[_find(parent, component[0])]]
                    ),
                )
                for index in component
            ),
        )
        for number, component in enumerate(components, start=1)
    )


def _validated_matrix(faces: Sequence[ClusterFaceValue]) -> NDArray[np.float64]:
    dimensions = len(faces[0].vector)
    if dimensions < 1 or any(len(face.vector) != dimensions for face in faces):
        raise ValueError("face vectors must have the same dimensions")
    matrix = np.asarray([face.vector for face in faces], dtype=np.float64)
    if not np.isfinite(matrix).all():
        raise ValueError("face vectors must contain only finite values")
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(norms).all() or not np.allclose(
        norms, 1.0, rtol=0.0, atol=_NORMALIZATION_TOLERANCE
    ):
        raise ValueError("face vectors must be normalized")
    return matrix


def _candidate_edges(
    ids: tuple[str, ...],
    vectors: NDArray[np.float64],
    threshold: float,
    block_size: int,
    max_candidate_edges: int,
) -> list[tuple[float, int, int]]:
    edges: list[tuple[float, int, int]] = []
    for left_start in range(0, len(ids), block_size):
        left_end = min(left_start + block_size, len(ids))
        for right_start in range(left_start, len(ids), block_size):
            right_end = min(right_start + block_size, len(ids))
            distances = _cosine_distance_block(
                vectors[left_start:left_end], vectors[right_start:right_end]
            )
            candidates = np.argwhere(distances <= threshold)
            candidate_count = sum(
                left_start + int(left) < right_start + int(right) for left, right in candidates
            )
            if len(edges) + candidate_count > max_candidate_edges:
                raise CandidateEdgeLimitExceeded(max_candidate_edges)
            for left, right in candidates:
                left_index = left_start + int(left)
                right_index = right_start + int(right)
                if left_index < right_index:
                    edges.append((float(distances[left, right]), left_index, right_index))
    edges.sort(key=lambda edge: (edge[0], ids[edge[1]], ids[edge[2]]))
    return edges


def _component_medoid(
    component: tuple[int, ...], ids: tuple[str, ...], vectors: NDArray[np.float64], block_size: int
) -> int:
    if len(component) == 1:
        return component[0]
    selected = vectors[np.asarray(component)]
    sums = np.zeros(len(component), dtype=np.float64)
    for left_start in range(0, len(component), block_size):
        left_end = min(left_start + block_size, len(component))
        for right_start in range(0, len(component), block_size):
            right_end = min(right_start + block_size, len(component))
            sums[left_start:left_end] += _cosine_distance_block(
                selected[left_start:left_end], selected[right_start:right_end]
            ).sum(axis=1)
    means = sums / (len(component) - 1)
    lowest = float(means.min())
    return min(
        (index for index, mean in zip(component, means, strict=True) if mean == lowest),
        key=lambda index: ids[index],
    )


def _within_representative_radius(
    component: tuple[int, ...],
    representative: int,
    vectors: NDArray[np.float64],
    threshold: float,
    block_size: int,
) -> bool:
    for start in range(0, len(component), block_size):
        block = component[start : start + block_size]
        if not bool(
            np.all(
                _cosine_distance_block(
                    vectors[np.asarray(block)], vectors[representative : representative + 1]
                )[:, 0]
                <= threshold
            )
        ):
            return False
    return True


def _cosine_distance_block(
    left: NDArray[np.float64], right: NDArray[np.float64]
) -> NDArray[np.float64]:
    return np.clip(1.0 - np.dot(left, right.T), 0.0, _MAX_COSINE_DISTANCE)


def _cosine_distance(left: NDArray[np.float64], right: NDArray[np.float64]) -> float:
    return float(np.clip(1.0 - np.dot(left, right), 0.0, _MAX_COSINE_DISTANCE))


def _find(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _validate_threshold(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or not 0 <= float(value) <= 2
    ):
        raise ValueError(f"{name} must be between 0 and 2")


def _validate_positive_integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be positive")


def _validate_vector(vector: Sequence[float]) -> None:
    if not vector or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector
    ):
        raise ValueError("face vectors must contain only finite values")
    if any(not isfinite(float(value)) for value in vector):
        raise ValueError("face vectors must contain only finite values")
    norm = sqrt(fsum(float(value) * float(value) for value in vector))
    if not isfinite(norm) or abs(norm - 1.0) > _NORMALIZATION_TOLERANCE:
        raise ValueError("face vectors must be normalized")
