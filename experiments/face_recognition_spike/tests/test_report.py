from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from face_spike.artifacts import ExperimentResult, asset_relative_path
from face_spike.cli import RunConfig
from face_spike.domain import DatasetItem, FaceExpected, LoadedImage
from face_spike.pipeline import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    ImageAnalysis,
)
from face_spike.report import render_report
from face_spike.retrieval import evaluate_retrieval


def _config(tmp_path: Path) -> RunConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet-model")
    sface.write_bytes(b"sface-model")
    return RunConfig(
        photos=tmp_path / "photos",
        labels=tmp_path / "labels.csv",
        yunet_model=yunet,
        sface_model=sface,
        output=tmp_path / "unused",
        detection_threshold=0.75,
        min_face_px=32,
        top_k=10,
        max_image_dimension=12000,
        max_image_pixels=100_000_000,
    )


def _analysis(filename: str, group: str | None, vector: tuple[float, float]) -> ImageAnalysis:
    pixels = np.zeros((36, 48, 3), dtype=np.uint8)
    image = LoadedImage(DatasetItem(filename, group, FaceExpected.YES), pixels, pixels, 48, 36)
    detection = FaceDetection(
        BoundingBox(2, 3, 20, 16),
        FaceLandmarks((4, 5), (16, 5), (10, 10), (5, 15), (15, 15)),
        0.875,
        primary=True,
    )
    return ImageAnalysis(
        image, (detection,), detection, FaceEmbedding(np.asarray(vector, dtype=np.float32)), "ok"
    )


def test_report_escapes_text_uses_relative_images_and_limits_neighbors(tmp_path: Path) -> None:
    analyses = (
        _analysis('query<"&.jpg', "rider<&", (1.0, 0.0)),
        *tuple(_analysis(f"candidate-{index}.jpg", "rider<&", (0.0, 1.0)) for index in range(12)),
    )
    result = ExperimentResult(
        config=_config(tmp_path),
        analyses=analyses,
        retrieval=evaluate_retrieval(analyses, top_k=12),
        started_at=datetime(2026, 7, 22, tzinfo=UTC),
        finished_at=datetime(2026, 7, 22, tzinfo=UTC),
        durations={},
        unexpected_labelled_image_failures=0,
        dependency_versions={},
    )

    report = render_report(result, {"retrieval": {"recall_at_1": None}})

    assert "query&lt;&quot;&amp;.jpg" in report
    assert "rider&lt;&amp;" in report
    assert "&lt;32" in report
    assert "32–63" in report
    assert "64–127" in report
    assert "128–255" in report
    assert "&gt;=256" in report
    assert "annotated/" in report
    assert f"annotated/{asset_relative_path('query<"&.jpg', '.jpg').as_posix()}" in report
    assert str(tmp_path) not in report
    assert all(section.count("<li>") <= 10 for section in report.split("</section>"))
    assert "vector" not in report
    assert "1.0, 0.0" not in report


def test_report_shows_the_stable_top_ten_candidates_for_a_query(tmp_path: Path) -> None:
    analyses = (
        _analysis("query.jpg", "rider", (1.0, 0.0)),
        *tuple(_analysis(f"candidate-{index:02}.jpg", "rider", (0.0, 1.0)) for index in range(11)),
    )
    result = ExperimentResult(
        config=_config(tmp_path),
        analyses=analyses,
        retrieval=evaluate_retrieval(analyses, top_k=10),
        started_at=datetime(2026, 7, 22, tzinfo=UTC),
        finished_at=datetime(2026, 7, 22, tzinfo=UTC),
        durations={},
        unexpected_labelled_image_failures=0,
        dependency_versions={},
    )

    report = render_report(result, {"retrieval": {"recall_at_1": None}})
    query_section = report.split("<h3>query.jpg (rider)</h3>", 1)[1].split("</section>", 1)[0]

    assert query_section.count("<li>") == 10
    assert all(f"candidate-{index:02}.jpg" in query_section for index in range(10))
    assert "candidate-10.jpg" not in query_section
