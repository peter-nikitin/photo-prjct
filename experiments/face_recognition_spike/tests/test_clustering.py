from __future__ import annotations

import math

import numpy as np
import pytest
from face_spike.analysis import (
    BoundingBox,
    EventPhotoAnalysis,
    FaceDetection,
    FaceEmbedding,
    FaceInstance,
    FaceLandmarks,
    face_crop_path,
)
from face_spike.clustering import (
    FaceCluster,
    cluster_successful_faces,
    ordered_face_clusters,
)
from face_spike.quality import FaceQuality


def _face(
    face_id: str,
    vector: tuple[float, ...] | None,
    *,
    status: str = "ok",
) -> FaceInstance:
    filename = face_id.partition("#")[0]
    detection = FaceDetection(
        BoundingBox(0, 0, 10, 10),
        FaceLandmarks((0, 0), (1, 0), (0, 1), (0, 2), (1, 2)),
        0.9,
    )
    embedding = None if vector is None else FaceEmbedding(np.asarray(vector, dtype=np.float32))
    return FaceInstance(
        face_id,
        filename,
        1,
        detection,
        face_crop_path(face_id),
        status,
        embedding,
        FaceQuality(0.9, 10.0, 1.0, 100.0, "accepted", ()),
    )


def _analyses(*faces: FaceInstance) -> tuple[EventPhotoAnalysis, ...]:
    return tuple(EventPhotoAnalysis(face.filename, 10, 10, (face,), "ok") for face in faces)


def _snapshot(analyses: tuple[EventPhotoAnalysis, ...], block_size: int) -> tuple[object, ...]:
    clusters = cluster_successful_faces(
        analyses,
        cluster_threshold=0.2,
        representative_threshold=0.2,
        distance_block_size=block_size,
    )
    return tuple(
        (
            cluster.cluster_id,
            cluster.representative_face_id,
            tuple(
                (member.face_id, round(member.distance_to_representative, 6))
                for member in cluster.members
            ),
        )
        for cluster in clusters
    )


def test_clustering_includes_candidate_edges_at_the_threshold_boundary() -> None:
    boundary = (0, 1)

    clusters = cluster_successful_faces(
        _analyses(_face("bravo.jpg#face-001", boundary), _face("alpha.jpg#face-001", (1, 0))),
        cluster_threshold=1.0,
        representative_threshold=1.0,
        distance_block_size=1,
    )

    assert [(cluster.cluster_id, cluster.representative_face_id) for cluster in clusters] == [
        ("person-0001", "alpha.jpg#face-001")
    ]
    assert [member.face_id for member in clusters[0].members] == [
        "alpha.jpg#face-001",
        "bravo.jpg#face-001",
    ]
    assert [member.distance_to_representative for member in clusters[0].members] == pytest.approx(
        [0.0, 1.0]
    )


def test_clustering_output_is_deterministic_for_repeated_and_reordered_analyses() -> None:
    faces = (
        _face("charlie.jpg#face-001", (0, 1)),
        _face("alpha.jpg#face-001", (1, 0)),
        _face("bravo.jpg#face-001", (0.9, math.sqrt(0.19))),
    )

    first = _snapshot(_analyses(*faces), 2)
    second = _snapshot(_analyses(*reversed(faces)), 2)

    assert first == second
    assert first == (
        (
            "person-0001",
            "alpha.jpg#face-001",
            (
                ("alpha.jpg#face-001", 0.0),
                ("bravo.jpg#face-001", 0.1),
            ),
        ),
        (
            "person-0002",
            "charlie.jpg#face-001",
            (("charlie.jpg#face-001", 0.0),),
        ),
    )


def test_clustering_membership_is_independent_of_distance_block_size() -> None:
    faces = (
        _face("echo.jpg#face-001", (0, 1)),
        _face("charlie.jpg#face-001", (0.8, 0.6)),
        _face("alpha.jpg#face-001", (1, 0)),
        _face("delta.jpg#face-001", (-1, 0)),
        _face("bravo.jpg#face-001", (0.98, math.sqrt(1 - 0.98**2))),
    )

    snapshots = {_snapshot(_analyses(*faces), block_size) for block_size in (1, 2, 3, 5)}

    assert len(snapshots) == 1


def test_clustering_retains_successful_singletons_and_excludes_failed_embeddings() -> None:
    clusters = cluster_successful_faces(
        _analyses(
            _face("alpha.jpg#face-001", (1, 0)),
            _face("broken.jpg#face-001", None, status="embedding_failed"),
            _face("bravo.jpg#face-001", (0, 1)),
        ),
        cluster_threshold=0.1,
        representative_threshold=0.1,
        distance_block_size=2,
    )

    assert [
        (
            cluster.cluster_id,
            cluster.representative_face_id,
            [member.face_id for member in cluster.members],
        )
        for cluster in clusters
    ] == [
        ("person-0001", "alpha.jpg#face-001", ["alpha.jpg#face-001"]),
        ("person-0002", "bravo.jpg#face-001", ["bravo.jpg#face-001"]),
    ]


def test_representative_guard_rejects_an_obvious_single_link_chain_merge() -> None:
    unit_circle = tuple(
        _face(
            f"{name}.jpg#face-001",
            (math.cos(math.radians(angle)), math.sin(math.radians(angle))),
        )
        for name, angle in (("alpha", 0), ("bravo", 60), ("charlie", 120), ("delta", 180))
    )

    clusters = cluster_successful_faces(
        _analyses(*unit_circle),
        cluster_threshold=0.500001,
        representative_threshold=0.500001,
        distance_block_size=2,
    )

    assert [
        (cluster.representative_face_id, [member.face_id for member in cluster.members])
        for cluster in clusters
    ] == [
        (
            "alpha.jpg#face-001",
            ["alpha.jpg#face-001", "bravo.jpg#face-001"],
        ),
        (
            "charlie.jpg#face-001",
            ["charlie.jpg#face-001", "delta.jpg#face-001"],
        ),
    ]


def test_clustering_uses_the_unique_lowest_mean_distance_as_the_medoid() -> None:
    faces = tuple(
        _face(
            f"{name}.jpg#face-001",
            (math.cos(angle), math.sin(angle)),
        )
        for name, angle in (("alpha", 0.0), ("bravo", 1e-6), ("charlie", 2e-6))
    )

    clusters = cluster_successful_faces(
        _analyses(*faces),
        cluster_threshold=1e-12,
        representative_threshold=1e-12,
        distance_block_size=2,
    )

    assert [
        (cluster.representative_face_id, [member.face_id for member in cluster.members])
        for cluster in clusters
    ] == [
        (
            "bravo.jpg#face-001",
            ["alpha.jpg#face-001", "bravo.jpg#face-001", "charlie.jpg#face-001"],
        )
    ]


def test_clustering_rejects_duplicate_successful_face_ids() -> None:
    with pytest.raises(ValueError, match="successful face IDs must be unique"):
        cluster_successful_faces(
            _analyses(
                _face("frame.jpg#face-001", (1, 0)),
                _face("frame.jpg#face-001", (0, 1)),
            ),
            cluster_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=1,
        )


@pytest.mark.parametrize("block_size", [1, 2, 5])
def test_dense_candidate_graph_fails_at_the_explicit_edge_limit(block_size: int) -> None:
    faces = tuple(_face(f"frame-{index}.jpg#face-001", (1, 0)) for index in range(5))

    with pytest.raises(RuntimeError, match="candidate edge limit exceeded: 4"):
        cluster_successful_faces(
            _analyses(*faces),
            cluster_threshold=2.0,
            representative_threshold=2.0,
            distance_block_size=block_size,
            max_candidate_edges=4,
        )


def test_cluster_artifact_order_is_numeric_across_five_digit_boundary() -> None:
    clusters = (
        FaceCluster("person-10000", "z.jpg#face-001", ()),
        FaceCluster("person-1001", "a.jpg#face-001", ()),
    )

    ordered = ordered_face_clusters(clusters)

    assert [cluster.cluster_id for cluster in ordered] == [
        "person-1001",
        "person-10000",
    ]
