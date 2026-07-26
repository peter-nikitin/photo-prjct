from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from face_spike.quality import FaceQualityThresholds
from face_spike.quality_calibration import build_quality_calibration
from PIL import Image


def test_calibration_preserves_cluster_ids_and_reports_label_outcomes(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "faces").mkdir(parents=True)
    Image.fromarray(np.zeros((20, 20, 3), dtype=np.uint8)).save(run / "faces" / "a.png")
    Image.fromarray(np.indices((20, 20)).sum(axis=0).astype(np.uint8)).save(run / "faces" / "b.png")
    (run / "faces.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "filename": "one.jpg",
                        "width": 100,
                        "height": 100,
                        "faces": [
                            {
                                "face_id": "one.jpg#face-001",
                                "confidence": 0.8,
                                "width": 10,
                                "height": 10,
                                "crop_path": "faces/a.png",
                            },
                            {
                                "face_id": "one.jpg#face-002",
                                "confidence": 0.9,
                                "width": 20,
                                "height": 20,
                                "crop_path": "faces/b.png",
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run / "clusters.json").write_text(
        json.dumps(
            {
                "clusters": [
                    {
                        "cluster_id": "person-0001",
                        "members": [{"face_id": "one.jpg#face-001"}],
                    },
                    {
                        "cluster_id": "person-0002",
                        "members": [{"face_id": "one.jpg#face-002"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("bundle_id", "cluster_id", "quality"))
        writer.writerow(("bundle", "person-0001", "not_face"))
        writer.writerow(("bundle", "person-0002", "usable"))

    result = build_quality_calibration(
        run,
        labels,
        FaceQualityThresholds(
            minimum_confidence=0.82,
            minimum_face_px=1,
            minimum_relative_area=0,
            minimum_sharpness=0,
        ),
    )

    assert result["summary"] == {
        "reviewed_clusters": 2,
        "usable_retained": 1,
        "usable_total": 1,
        "not_face_rejected": 1,
        "not_face_total": 1,
        "low_quality_fully_rejected": 0,
        "low_quality_total": 0,
    }
    assert [row["cluster_id"] for row in result["clusters"]] == [
        "person-0001",
        "person-0002",
    ]
