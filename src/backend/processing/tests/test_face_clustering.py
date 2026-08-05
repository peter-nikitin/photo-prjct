from __future__ import annotations

import math
from uuid import UUID, uuid4

import numpy as np
import pytest
from numpy.typing import NDArray

from processing.services.face_clustering import (
    BuiltFaceCluster,
    CandidateEdgeLimitExceeded,
    ClusterFace,
    ClusterMember,
    build_face_clusters,
)


def _id(number: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{number:012d}")


def _face(number: int, vector: tuple[float, ...]) -> ClusterFace:
    return ClusterFace(face_id=_id(number), vector=vector)


def _snapshot(faces: tuple[ClusterFace, ...], *, block_size: int = 2) -> tuple[object, ...]:
    return tuple(
        (
            cluster.cluster_key,
            cluster.representative_face_id,
            tuple(
                (member.face_id, member.distance_to_representative) for member in cluster.members
            ),
        )
        for cluster in build_face_clusters(
            faces,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=block_size,
            max_candidate_edges=100,
        )
    )


def test_repeated_and_reordered_input_has_stable_clusters_and_keys() -> None:
    faces = (
        _face(3, (0.0, 1.0)),
        _face(1, (1.0, 0.0)),
        _face(2, (0.9, math.sqrt(0.19))),
    )

    first = _snapshot(faces)
    second = _snapshot(tuple(reversed(faces)))

    assert first == second
    assert first == (
        (
            "cluster-0001",
            _id(1),
            ((_id(1), 0.0), (_id(2), pytest.approx(0.1))),
        ),
        ("cluster-0002", _id(3), ((_id(3), 0.0),)),
    )


def test_medoid_tie_uses_smallest_face_identity() -> None:
    faces = (
        _face(4, (0.0, -1.0)),
        _face(2, (0.0, 1.0)),
        _face(3, (-1.0, 0.0)),
        _face(1, (1.0, 0.0)),
    )

    clusters = build_face_clusters(
        faces,
        edge_threshold=2.0,
        representative_threshold=2.0,
        distance_block_size=2,
        max_candidate_edges=100,
    )

    assert len(clusters) == 1
    assert clusters[0].representative_face_id == _id(1)


def test_representative_guard_rejects_bridge_merge() -> None:
    faces = tuple(
        _face(
            number,
            (math.cos(math.radians(angle)), math.sin(math.radians(angle))),
        )
        for number, angle in ((1, 0), (2, 20), (3, 80), (4, 100))
    )

    clusters = build_face_clusters(
        faces,
        edge_threshold=0.51,
        representative_threshold=0.4,
        distance_block_size=2,
        max_candidate_edges=100,
    )

    assert [
        (cluster.representative_face_id, tuple(member.face_id for member in cluster.members))
        for cluster in clusters
    ] == [
        (_id(1), (_id(1), _id(2))),
        (_id(3), (_id(3), _id(4))),
    ]


def test_unmerged_faces_are_preserved_as_singletons() -> None:
    clusters = build_face_clusters(
        (_face(2, (0.0, 1.0)), _face(1, (1.0, 0.0))),
        edge_threshold=0.1,
        representative_threshold=0.1,
        distance_block_size=2,
        max_candidate_edges=100,
    )

    assert [
        (cluster.cluster_key, cluster.representative_face_id, cluster.members)
        for cluster in clusters
    ] == [
        ("cluster-0001", _id(1), (ClusterMember(_id(1), 0.0),)),
        ("cluster-0002", _id(2), (ClusterMember(_id(2), 0.0),)),
    ]


@pytest.mark.parametrize(
    ("faces", "match"),
    [
        (
            (_face(1, (1.0, 0.0)), ClusterFace(_id(1), (0.0, 1.0))),
            "face IDs must be unique",
        ),
        ((_face(1, (1.0, 0.0, 0.0)), _face(2, (0.0, 1.0))), "same dimensions"),
    ],
)
def test_invalid_faces_are_rejected(faces: tuple[ClusterFace, ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build_face_clusters(
            faces,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )


@pytest.mark.parametrize(
    ("vector", "match"),
    [((2.0, 0.0), "normalized"), ((float("nan"), 0.0), "finite")],
)
def test_cluster_face_rejects_invalid_vectors(vector: tuple[float, ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ClusterFace(_id(1), vector)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"edge_threshold": -0.1}, "edge_threshold"),
        ({"representative_threshold": float("inf")}, "representative_threshold"),
        ({"distance_block_size": 0}, "distance_block_size"),
        ({"max_candidate_edges": 0}, "max_candidate_edges"),
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, float | int], match: str) -> None:
    configuration: dict[str, float | int] = {
        "edge_threshold": 0.1,
        "representative_threshold": 0.1,
        "distance_block_size": 2,
        "max_candidate_edges": 100,
    }
    configuration.update(kwargs)

    with pytest.raises(ValueError, match=match):
        build_face_clusters(
            (_face(1, (1.0, 0.0)),),
            edge_threshold=float(configuration["edge_threshold"]),
            representative_threshold=float(configuration["representative_threshold"]),
            distance_block_size=int(configuration["distance_block_size"]),
            max_candidate_edges=int(configuration["max_candidate_edges"]),
        )


def test_distance_work_is_bounded_by_requested_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    import processing.services.face_clustering as module

    seen: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    original = module._cosine_distance_block

    def bounded(left: NDArray[np.float64], right: NDArray[np.float64]) -> NDArray[np.float64]:
        seen.append((left.shape, right.shape))
        return original(left, right)

    monkeypatch.setattr(module, "_cosine_distance_block", bounded)
    build_face_clusters(
        tuple(_face(index, (1.0, 0.0)) for index in range(1, 6)),
        edge_threshold=0.1,
        representative_threshold=0.1,
        distance_block_size=2,
        max_candidate_edges=100,
    )

    assert seen
    assert all(left[0] <= 2 and right[0] <= 2 for left, right in seen)


def test_candidate_edge_limit_is_enforced_before_union() -> None:
    faces = tuple(_face(index, (1.0, 0.0)) for index in range(1, 5))

    with pytest.raises(CandidateEdgeLimitExceeded, match="candidate edge limit exceeded: 2") as exc:
        build_face_clusters(
            faces,
            edge_threshold=0.0,
            representative_threshold=0.0,
            distance_block_size=2,
            max_candidate_edges=2,
        )

    assert exc.value.limit == 2


def test_value_types_are_immutable_and_use_uuid_face_identities() -> None:
    face = ClusterFace(_id(1), (1.0, 0.0))
    member = ClusterMember(_id(1), 0.0)
    cluster = BuiltFaceCluster("cluster-0001", _id(1), (member,))

    with pytest.raises((AttributeError, TypeError)):
        face.face_id = uuid4()  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        member.distance_to_representative = 1.0  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        cluster.members = ()  # type: ignore[misc]
