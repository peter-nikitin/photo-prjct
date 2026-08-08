from __future__ import annotations

import numpy as np
from face_spike.analysis import (
    BoundingBox,
    EventPhotoAnalysis,
    FaceDetection,
    FaceEmbedding,
    FaceInstance,
    FaceLandmarks,
    face_crop_path,
)
from face_spike.cluster_report import render_cluster_report
from face_spike.clustering import ClusterMember, FaceCluster
from face_spike.quality import FaceQuality


def test_report_has_stable_person_anchors_relative_assets_and_escaped_text() -> None:
    filename = 'group<"&.jpg'
    detection = FaceDetection(
        BoundingBox(2, 3, 10, 12),
        FaceLandmarks((4, 5), (8, 5), (6, 7), (4, 10), (8, 10)),
        0.9,
    )
    face = FaceInstance(
        f"{filename}#face-001",
        filename,
        1,
        detection,
        face_crop_path(f"{filename}#face-001"),
        "ok",
        FaceEmbedding(np.asarray([1.0, 0.0], dtype=np.float32)),
        FaceQuality(
            "normalized-laplacian-v1",
            112,
            0.9,
            10.0,
            0.125,
            100.0,
            "accepted",
            (),
        ),
    )
    analysis = EventPhotoAnalysis(filename, 40, 24, (face,), "ok")
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))

    report = render_cluster_report(
        (analysis,),
        (cluster,),
        {"counts": {"clusters": 1, "singleton_clusters": 1}},
    )

    assert 'id="person-0001"' in report
    assert 'href="people/person-0001/index.html"' in report
    assert "group&lt;&quot;&amp;.jpg" in report
    assert "people/person-0001/faces/" in report
    assert "photos/group%3C%22%26.jpg" not in report
    assert 'loading="lazy"' in report
    assert "embedding" not in report.lower()
    assert "PRIMARY" not in report


def test_report_output_is_independent_of_input_order() -> None:
    first = FaceCluster("person-0001", "a.jpg#face-001", (ClusterMember("a.jpg#face-001", 0.0),))
    second = FaceCluster("person-0002", "b.jpg#face-001", (ClusterMember("b.jpg#face-001", 0.0),))

    forward = render_cluster_report((), (first, second), {"counts": {}})
    reverse = render_cluster_report((), (second, first), {"counts": {}})

    assert forward == reverse
