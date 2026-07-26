from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from re import compile as compile_regex

import numpy as np
from numpy.typing import NDArray

from .analysis import EventPhotoAnalysis, FaceInstance

_CLUSTER_ID_PATTERN = compile_regex(r"person-([0-9]{4,})")
DEFAULT_MAX_CANDIDATE_EDGES = 100_000


class CandidateEdgeLimitExceeded(RuntimeError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"candidate edge limit exceeded: {limit}")


@dataclass(frozen=True)
class ClusterMember:
    face_id: str
    distance_to_representative: float


@dataclass(frozen=True)
class FaceCluster:
    cluster_id: str
    representative_face_id: str
    members: tuple[ClusterMember, ...]


def ordered_face_clusters(clusters: Iterable[FaceCluster]) -> tuple[FaceCluster, ...]:
    """Return clusters in stable numeric ID order, including five-digit IDs."""
    return tuple(sorted(clusters, key=lambda cluster: cluster_id_number(cluster.cluster_id)))


def cluster_id_number(cluster_id: str) -> int:
    match = _CLUSTER_ID_PATTERN.fullmatch(cluster_id)
    if match is None:
        raise ValueError("invalid stable cluster ID")
    number = int(match.group(1))
    if number < 1 or cluster_id != f"person-{number:04d}":
        raise ValueError("invalid stable cluster ID")
    return number


def cluster_successful_faces(
    analyses: Iterable[EventPhotoAnalysis],
    *,
    cluster_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int = DEFAULT_MAX_CANDIDATE_EDGES,
) -> tuple[FaceCluster, ...]:
    """Cluster successful face embeddings with deterministic guarded graph unions."""
    _validate_threshold(cluster_threshold, "cluster_threshold")
    _validate_threshold(representative_threshold, "representative_threshold")
    if distance_block_size < 1:
        raise ValueError("distance_block_size must be positive")
    if max_candidate_edges < 1:
        raise ValueError("max_candidate_edges must be positive")

    faces = _successful_faces(analyses)
    if not faces:
        return ()

    face_ids = tuple(face.face_id for face in faces)
    if len(set(face_ids)) != len(face_ids):
        raise ValueError("successful face IDs must be unique")
    vectors = _normalized_vectors(faces)
    parent = list(range(len(faces)))
    members: dict[int, tuple[int, ...]] = {index: (index,) for index in range(len(faces))}
    representatives = {index: index for index in range(len(faces))}

    for _, left_index, right_index in _candidate_edges(
        face_ids,
        vectors,
        cluster_threshold,
        distance_block_size,
        max_candidate_edges,
    ):
        left_root = _find(parent, left_index)
        right_root = _find(parent, right_index)
        if left_root == right_root:
            continue

        proposed_members = tuple(sorted((*members[left_root], *members[right_root])))
        proposed_representative = _component_medoid(
            proposed_members, face_ids, vectors, distance_block_size
        )
        if not _within_representative_radius(
            proposed_members, proposed_representative, vectors, representative_threshold
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
    clusters: list[FaceCluster] = []
    for number, component in enumerate(components, start=1):
        root = _find(parent, component[0])
        representative = representatives[root]
        clusters.append(
            FaceCluster(
                cluster_id=f"person-{number:04d}",
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


def _successful_faces(analyses: Iterable[EventPhotoAnalysis]) -> tuple[FaceInstance, ...]:
    return tuple(
        sorted(
            (
                face
                for analysis in analyses
                for face in analysis.faces
                if face.status == "ok" and face.embedding is not None
            ),
            key=lambda face: face.face_id,
        )
    )


def _normalized_vectors(faces: tuple[FaceInstance, ...]) -> NDArray[np.float64]:
    vectors = np.asarray([face.embedding.vector for face in faces if face.embedding is not None])
    vectors = vectors.astype(np.float64, copy=False)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def _candidate_edges(
    face_ids: tuple[str, ...],
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
                        (float(distances[left_offset, right_offset]), left_index, right_index)
                    )
    edges.sort(key=lambda edge: (edge[0], face_ids[edge[1]], face_ids[edge[2]]))
    return edges


def _component_medoid(
    component: tuple[int, ...],
    face_ids: tuple[str, ...],
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
    return min(tied, key=lambda index: face_ids[index])


def _within_representative_radius(
    component: tuple[int, ...],
    representative: int,
    vectors: NDArray[np.float64],
    threshold: float,
) -> bool:
    distances = _cosine_distance_block(
        vectors[np.asarray(component)], vectors[representative : representative + 1]
    )
    return bool(np.all(distances[:, 0] <= threshold))


def _cosine_distance_block(
    left: NDArray[np.float64], right: NDArray[np.float64]
) -> NDArray[np.float64]:
    return np.clip(1.0 - left @ right.T, 0.0, 2.0)


def _cosine_distance(left: NDArray[np.float64], right: NDArray[np.float64]) -> float:
    return float(np.clip(1.0 - np.dot(left, right), 0.0, 2.0))


def _find(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _validate_threshold(threshold: float, name: str) -> None:
    if not isfinite(threshold) or not 0 <= threshold <= 2:
        raise ValueError(f"{name} must be between 0 and 2")
