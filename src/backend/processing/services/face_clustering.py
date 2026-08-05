"""Django-facing UUID wrapper around the dependency-neutral clustering kernel."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from face_cluster_contract import (
    CandidateEdgeLimitExceeded,
    ClusterFaceValue,
    build_face_cluster_kernel,
)


@dataclass(frozen=True, slots=True)
class ClusterFace:
    face_id: UUID
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, UUID):
            raise ValueError("face_id must be a UUID")
        # Constructing the dependency-neutral value preserves the established
        # eager validation of the production UUID-facing API.
        ClusterFaceValue(self.face_id.hex, self.vector)


@dataclass(frozen=True, slots=True)
class ClusterMember:
    face_id: UUID
    distance_to_representative: float

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, UUID):
            raise ValueError("face_id must be a UUID")
        if (
            isinstance(self.distance_to_representative, bool)
            or not isinstance(self.distance_to_representative, (int, float))
            or not isfinite(float(self.distance_to_representative))
            or not 0 <= float(self.distance_to_representative) <= 2
        ):
            raise ValueError("distance_to_representative must be between 0 and 2")
        object.__setattr__(
            self, "distance_to_representative", float(self.distance_to_representative)
        )


@dataclass(frozen=True, slots=True)
class BuiltFaceCluster:
    cluster_key: str
    representative_face_id: UUID
    members: tuple[ClusterMember, ...]


def build_face_clusters(
    faces: Iterable[ClusterFace],
    *,
    edge_threshold: float,
    representative_threshold: float,
    distance_block_size: int,
    max_candidate_edges: int,
) -> tuple[BuiltFaceCluster, ...]:
    """Preserve the production UUID API while delegating exact clustering to the pure kernel."""
    values = tuple(
        ClusterFaceValue(face.face_id.hex, face.vector)
        for face in sorted(faces, key=lambda value: value.face_id.int)
    )
    return tuple(
        BuiltFaceCluster(
            cluster_key=cluster.cluster_key,
            representative_face_id=UUID(hex=cluster.representative_face_id),
            members=tuple(
                ClusterMember(UUID(hex=member.face_id), member.distance_to_representative)
                for member in cluster.members
            ),
        )
        for cluster in build_face_cluster_kernel(
            values,
            edge_threshold=edge_threshold,
            representative_threshold=representative_threshold,
            distance_block_size=distance_block_size,
            max_candidate_edges=max_candidate_edges,
        )
    )


__all__: Sequence[str] = (
    "BuiltFaceCluster",
    "CandidateEdgeLimitExceeded",
    "ClusterFace",
    "ClusterMember",
    "build_face_clusters",
)
