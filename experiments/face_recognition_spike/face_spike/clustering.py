"""Experiment-facing adapter for the shared deterministic clustering kernel."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from re import compile as compile_regex

import numpy as np
from face_cluster_contract import (
    CandidateEdgeLimitExceeded,
    ClusterFaceValue,
    build_face_cluster_kernel,
)

from .analysis import EventPhotoAnalysis, FaceInstance

_CLUSTER_ID_PATTERN = compile_regex(r"person-([0-9]{4,})")


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
    max_candidate_edges: int,
) -> tuple[FaceCluster, ...]:
    """Use the exact production kernel with the recorded artifact limits."""
    faces = _successful_faces(analyses)
    if not faces:
        return ()
    face_ids = tuple(face.face_id for face in faces)
    if len(set(face_ids)) != len(face_ids):
        raise ValueError("successful face IDs must be unique")
    vectors = _normalized_vectors(faces)
    built = build_face_cluster_kernel(
        tuple(
            ClusterFaceValue(face.face_id, tuple(float(value) for value in vectors[index]))
            for index, face in enumerate(faces)
        ),
        edge_threshold=cluster_threshold,
        representative_threshold=representative_threshold,
        distance_block_size=distance_block_size,
        max_candidate_edges=max_candidate_edges,
    )
    return tuple(
        FaceCluster(
            cluster_id=f"person-{number:04d}",
            representative_face_id=cluster.representative_face_id,
            members=tuple(
                ClusterMember(member.face_id, member.distance_to_representative)
                for member in cluster.members
            ),
        )
        for number, cluster in enumerate(built, start=1)
    )


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


def _normalized_vectors(faces: tuple[FaceInstance, ...]) -> np.ndarray:
    vectors = np.asarray([face.embedding.vector for face in faces if face.embedding is not None])
    vectors = vectors.astype(np.float64, copy=False)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


__all__ = (
    "CandidateEdgeLimitExceeded",
    "ClusterMember",
    "FaceCluster",
    "cluster_id_number",
    "cluster_successful_faces",
    "ordered_face_clusters",
)
