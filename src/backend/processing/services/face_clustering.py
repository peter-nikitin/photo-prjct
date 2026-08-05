"""Deterministic, guarded clustering of compatible gallery face embeddings."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import fsum, isfinite, sqrt
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

_NORMALIZATION_TOLERANCE = 1e-6
_MAX_COSINE_DISTANCE = 2.0


class CandidateEdgeLimitExceeded(RuntimeError):
    """The exact candidate graph exceeded its configured bounded edge count."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"candidate edge limit exceeded: {limit}")


@dataclass(frozen=True, slots=True)
class ClusterFace:
    """One accepted gallery face and its finite, normalized embedding."""

    face_id: UUID
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, UUID):
            raise ValueError("face_id must be a UUID")
        if not isinstance(self.vector, tuple):
            raise ValueError("vector must be a tuple")
        _validate_vector(self.vector)


@dataclass(frozen=True, slots=True)
class ClusterMember:
    """A face in a cluster and its distance from the cluster representative."""

    face_id: UUID
    distance_to_representative: float

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, UUID):
            raise ValueError("face_id must be a UUID")
        distance = self.distance_to_representative
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise ValueError("distance_to_representative must be finite")
        if not isfinite(float(distance)) or not 0.0 <= float(distance) <= _MAX_COSINE_DISTANCE:
            raise ValueError("distance_to_representative must be between 0 and 2")
        object.__setattr__(self, "distance_to_representative", float(distance))


@dataclass(frozen=True, slots=True)
class BuiltFaceCluster:
    """One anonymous deterministic cluster produced by :func:`build_face_clusters`."""

    cluster_key: str
    representative_face_id: UUID
    members: tuple[ClusterMember, ...]

    def __post_init__(self) -> None:
        if not self.cluster_key:
            raise ValueError("cluster_key must not be empty")
        if not isinstance(self.representative_face_id, UUID):
            raise ValueError("representative_face_id must be a UUID")
        if not isinstance(self.members, tuple):
            raise ValueError("members must be a tuple")
        member_ids = tuple(member.face_id for member in self.members)
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("cluster members must have unique face IDs")
        if self.representative_face_id not in member_ids:
            raise ValueError("representative face must be a cluster member")


def build_face_clusters(
    faces: Iterable[ClusterFace],
    *,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
) -> tuple[BuiltFaceCluster, ...]:
    """Build deterministic anonymous face clusters with a medoid-radius merge guard.

    Candidate distances are computed exactly in bounded NumPy blocks.  Edges are processed in
    ascending distance and UUID order, and every accepted merge is checked against the proposed
    component's recomputed medoid.  Faces that never merge are retained as singleton clusters.
    """

    _validate_threshold(edge_threshold, "edge_threshold")
    _validate_threshold(representative_threshold, "representative_threshold")
    _validate_positive_integer(distance_block_size, "distance_block_size")
    _validate_positive_integer(max_candidate_edges, "max_candidate_edges")

    ordered_faces = tuple(sorted(faces, key=lambda face: face.face_id.int))
    face_ids = tuple(face.face_id for face in ordered_faces)
    if len(set(face_ids)) != len(face_ids):
        raise ValueError("face IDs must be unique")
    if not ordered_faces:
        return ()

    vectors = _validated_matrix(ordered_faces)
    parent = list(range(len(ordered_faces)))
    members: dict[int, tuple[int, ...]] = {index: (index,) for index in range(len(ordered_faces))}
    representatives: dict[int, int] = {index: index for index in range(len(ordered_faces))}

    edges = _candidate_edges(
        face_ids,
        vectors,
        edge_threshold,
        distance_block_size,
        max_candidate_edges,
    )
    for _, left_index, right_index in edges:
        left_root = _find(parent, left_index)
        right_root = _find(parent, right_index)
        if left_root == right_root:
            continue

        proposed_members = tuple(sorted((*members[left_root], *members[right_root])))
        proposed_representative = _component_medoid(
            proposed_members,
            face_ids,
            vectors,
            distance_block_size,
        )
        if not _within_representative_radius(
            proposed_members,
            proposed_representative,
            vectors,
            representative_threshold,
            distance_block_size,
        ):
            continue

        new_root, absorbed_root = sorted((left_root, right_root))
        parent[absorbed_root] = new_root
        members[new_root] = proposed_members
        representatives[new_root] = proposed_representative
        del members[absorbed_root]
        del representatives[absorbed_root]

    components = sorted(
        members.values(), key=lambda indices: tuple(face_ids[index] for index in indices)
    )
    clusters: list[BuiltFaceCluster] = []
    for number, component in enumerate(components, start=1):
        root = _find(parent, component[0])
        representative = representatives[root]
        clusters.append(
            BuiltFaceCluster(
                cluster_key=f"cluster-{number:04d}",
                representative_face_id=face_ids[representative],
                members=tuple(
                    ClusterMember(
                        face_id=face_ids[index],
                        distance_to_representative=_cosine_distance(
                            vectors[index], vectors[representative]
                        ),
                    )
                    for index in component
                ),
            )
        )
    return tuple(clusters)


def _validated_matrix(faces: tuple[ClusterFace, ...]) -> NDArray[np.float64]:
    dimensions = len(faces[0].vector)
    if dimensions < 1:
        raise ValueError("face vectors must not be empty")
    if any(len(face.vector) != dimensions for face in faces):
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
    face_ids: tuple[UUID, ...],
    vectors: NDArray[np.float64],
    threshold: float,
    block_size: int,
    max_candidate_edges: int,
) -> list[tuple[float, int, int]]:
    edges: list[tuple[float, int, int]] = []
    for left_start in range(0, len(face_ids), block_size):
        left_end = min(left_start + block_size, len(face_ids))
        for right_start in range(left_start, len(face_ids), block_size):
            right_end = min(right_start + block_size, len(face_ids))
            distances = _cosine_distance_block(
                vectors[left_start:left_end], vectors[right_start:right_end]
            )
            candidate_coordinates = np.argwhere(distances <= threshold)
            candidate_count = sum(
                left_start + int(left_offset) < right_start + int(right_offset)
                for left_offset, right_offset in candidate_coordinates
            )
            if len(edges) + candidate_count > max_candidate_edges:
                raise CandidateEdgeLimitExceeded(max_candidate_edges)
            for left_offset, right_offset in candidate_coordinates:
                left_index = left_start + int(left_offset)
                right_index = right_start + int(right_offset)
                if left_index < right_index:
                    edges.append(
                        (
                            float(distances[left_offset, right_offset]),
                            left_index,
                            right_index,
                        )
                    )
    edges.sort(key=lambda edge: (edge[0], face_ids[edge[1]], face_ids[edge[2]]))
    return edges


def _component_medoid(
    component: tuple[int, ...],
    face_ids: tuple[UUID, ...],
    vectors: NDArray[np.float64],
    block_size: int,
) -> int:
    if len(component) == 1:
        return component[0]
    component_vectors = vectors[np.asarray(component)]
    distance_sums = np.zeros(len(component), dtype=np.float64)
    for left_start in range(0, len(component), block_size):
        left_end = min(left_start + block_size, len(component))
        for right_start in range(0, len(component), block_size):
            right_end = min(right_start + block_size, len(component))
            distances = _cosine_distance_block(
                component_vectors[left_start:left_end], component_vectors[right_start:right_end]
            )
            distance_sums[left_start:left_end] += distances.sum(axis=1)
    mean_distances = distance_sums / (len(component) - 1)
    lowest_mean = float(mean_distances.min())
    tied = (
        index
        for index, mean_distance in zip(component, mean_distances, strict=True)
        if mean_distance == lowest_mean
    )
    return min(tied, key=lambda index: face_ids[index].int)


def _within_representative_radius(
    component: tuple[int, ...],
    representative: int,
    vectors: NDArray[np.float64],
    threshold: float,
    block_size: int,
) -> bool:
    representative_vector = vectors[representative : representative + 1]
    for start in range(0, len(component), block_size):
        block = component[start : start + block_size]
        distances = _cosine_distance_block(vectors[np.asarray(block)], representative_vector)
        if not bool(np.all(distances[:, 0] <= threshold)):
            return False
    return True


def _cosine_distance_block(
    left: NDArray[np.float64], right: NDArray[np.float64]
) -> NDArray[np.float64]:
    return np.clip(1.0 - left @ right.T, 0.0, _MAX_COSINE_DISTANCE)


def _cosine_distance(left: NDArray[np.float64], right: NDArray[np.float64]) -> float:
    return float(np.clip(1.0 - np.dot(left, right), 0.0, _MAX_COSINE_DISTANCE))


def _find(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _validate_threshold(threshold: float, name: str) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(f"{name} must be between 0 and 2")
    if not isfinite(float(threshold)) or not 0.0 <= float(threshold) <= _MAX_COSINE_DISTANCE:
        raise ValueError(f"{name} must be between 0 and 2")


def _validate_positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be positive")


def _validate_vector(vector: Sequence[float]) -> None:
    if not vector:
        raise ValueError("face vectors must not be empty")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in vector):
        raise ValueError("face vectors must contain only finite values")
    if any(not isfinite(float(item)) for item in vector):
        raise ValueError("face vectors must contain only finite values")
    norm = sqrt(fsum(float(item) * float(item) for item in vector))
    if not isfinite(norm) or abs(norm - 1.0) > _NORMALIZATION_TOLERANCE:
        raise ValueError("face vectors must be normalized")
