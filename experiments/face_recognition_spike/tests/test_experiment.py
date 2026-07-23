from __future__ import annotations

import csv
from pathlib import Path
from typing import get_args

import numpy as np
import pytest
from face_spike import cli
from face_spike.domain import ErrorCode, FaceExpected
from face_spike.pipeline import (
    AnalysisStatus,
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
)
from fixtures import make_jpeg


def _write_labels(photos: Path, rows: list[tuple[str, str, str]]) -> Path:
    labels = photos.parent / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("filename", "participant_group", "face_expected"))
        writer.writerows(rows)
    return labels


def _models(root: Path) -> tuple[Path, Path]:
    yunet = root / "yunet.onnx"
    sface = root / "sface.onnx"
    yunet.write_bytes(b"fake-yunet")
    sface.write_bytes(b"fake-sface")
    return yunet, sface


def _config(root: Path, photos: Path, labels: Path, output: Path) -> cli.RunConfig:
    yunet, sface = _models(root)
    return cli.RunConfig(
        photos=photos,
        labels=labels,
        yunet_model=yunet,
        sface_model=sface,
        output=output,
        detection_threshold=0.75,
        min_face_px=1,
        top_k=10,
        max_image_dimension=100,
        max_image_pixels=10_000,
    )


def _detection() -> FaceDetection:
    return FaceDetection(
        BoundingBox(0, 0, 2, 2),
        FaceLandmarks((0, 0), (1, 0), (1, 1), (0, 1), (1, 1)),
        0.9,
    )


def test_analysis_status_includes_recoverable_dataset_error_codes() -> None:
    assert ErrorCode in get_args(AnalysisStatus)


class _Detector:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.widths: list[int] = []

    def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
        self.widths.append(bgr.shape[1])
        return () if bgr.shape[1] == 4 else (_detection(),)


class _Recognizer:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.widths: list[int] = []

    def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
        self.widths.append(bgr.shape[1])
        if bgr.shape[1] == 5:
            raise RuntimeError("embedding backend detail")
        return FaceEmbedding(np.asarray([1.0, 0.0], dtype=np.float32))


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[_Detector], list[_Recognizer]]:
    detectors: list[_Detector] = []
    recognizers: list[_Recognizer] = []

    def detector_factory(*args: object, **kwargs: object) -> _Detector:
        detector = _Detector(*args, **kwargs)
        detectors.append(detector)
        return detector

    def recognizer_factory(*args: object, **kwargs: object) -> _Recognizer:
        recognizer = _Recognizer(*args, **kwargs)
        recognizers.append(recognizer)
        return recognizer

    monkeypatch.setattr(cli, "YuNetDetector", detector_factory)
    monkeypatch.setattr(cli, "SFaceRecognizer", recognizer_factory)
    return detectors, recognizers


def test_run_processes_filename_order_and_writes_complete_evidence_after_partial_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "alpha.jpg", size=(3, 2))
    (photos / "broken.jpg").write_bytes(b"not a JPEG")
    make_jpeg(photos / "no-face.jpg", size=(4, 2))
    make_jpeg(photos / "problem.jpg", size=(5, 2))
    labels = _write_labels(
        photos,
        [
            ("problem.jpg", "rider-a", "yes"),
            ("no-face.jpg", "rider-a", "yes"),
            ("broken.jpg", "rider-a", "yes"),
            ("alpha.jpg", "rider-a", "yes"),
        ],
    )
    detectors, recognizers = _install_fakes(monkeypatch)
    seen: list[str] = []
    original_load_image = cli.load_image

    def recording_load_image(*args: object, **kwargs: object) -> object:
        seen.append(args[0].filename)
        return original_load_image(*args, **kwargs)

    monkeypatch.setattr(cli, "load_image", recording_load_image)
    clock_values = iter(
        (
            0.0,
            1.0,
            1.0,
            3.0,
            3.0,
            6.0,
            6.0,
            10.0,
            10.0,
            15.0,
            15.0,
            21.0,
            21.0,
            28.0,
            28.0,
            36.0,
            36.0,
            45.0,
        )
    )
    monkeypatch.setattr(cli, "_perf_counter", lambda: next(clock_values))
    output = tmp_path / "run"

    result = cli.run_experiment(_config(tmp_path, photos, labels, output))

    assert seen == ["alpha.jpg", "broken.jpg", "no-face.jpg", "problem.jpg"]
    assert detectors[0].widths == [3, 4, 5]
    assert recognizers[0].widths == [3, 5]
    assert [analysis.status for analysis in result.analyses] == [
        "ok",
        "image_decode_failed",
        "no_detection",
        "embedding_failed",
    ]
    assert result.unexpected_labelled_image_failures == 2
    assert result.durations == {"decode": 17.0, "detection": 16.0, "embedding": 12.0}
    assert {path.name for path in output.iterdir()} == {
        "annotated",
        "crops",
        "detections.csv",
        "manifest.json",
        "metrics.json",
        "report.html",
        "retrieval.csv",
    }
    source_jpeg_bytes = {
        path.read_bytes() for path in photos.iterdir() if path.suffix.lower() in {".jpg", ".jpeg"}
    }
    assert all(
        path.read_bytes() not in source_jpeg_bytes for path in output.rglob("*") if path.is_file()
    )


def test_main_returns_zero_for_a_valid_no_face_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "no-face.jpg", size=(4, 2))
    labels = _write_labels(photos, [("no-face.jpg", "rider-a", "yes")])
    _install_fakes(monkeypatch)
    config = _config(tmp_path, photos, labels, tmp_path / "run")

    assert cli.main(_arguments(config)) == 0
    assert (tmp_path / "run" / "detections.csv").is_file()


def test_main_returns_three_only_for_unexpected_yes_row_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos = tmp_path / "photos"
    (photos / "broken.jpg").parent.mkdir()
    (photos / "broken.jpg").write_bytes(b"not a JPEG")
    labels = _write_labels(photos, [("broken.jpg", "rider-a", FaceExpected.YES.value)])
    _install_fakes(monkeypatch)
    config = _config(tmp_path, photos, labels, tmp_path / "run")

    assert cli.main(_arguments(config)) == 3
    assert (tmp_path / "run" / "metrics.json").is_file()


@pytest.mark.parametrize("face_expected", [FaceExpected.NO.value, FaceExpected.UNCERTAIN.value])
def test_main_returns_zero_and_publishes_artifacts_for_non_yes_image_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, face_expected: str
) -> None:
    photos = tmp_path / "photos"
    (photos / "broken.jpg").parent.mkdir()
    (photos / "broken.jpg").write_bytes(b"not a JPEG")
    labels = _write_labels(photos, [("broken.jpg", "", face_expected)])
    _install_fakes(monkeypatch)
    config = _config(tmp_path, photos, labels, tmp_path / "run")

    assert cli.main(_arguments(config)) == 0
    assert (config.output / "manifest.json").is_file()
    assert (config.output / "detections.csv").is_file()
    assert (config.output / "metrics.json").is_file()


def test_main_returns_two_and_creates_no_output_for_fatal_validation(
    tmp_path: Path,
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg")
    labels = _write_labels(photos, [("face.jpg", "rider-a", "yes")])
    config = _config(tmp_path, photos, labels, tmp_path / "run")

    labels.write_text("unexpected,header\nface.jpg,rider-a\n", encoding="utf-8")
    assert cli.main(_arguments(config)) == 2
    assert not config.output.exists()

    _write_labels(photos, [("face.jpg", "rider-a", "yes")])
    arguments = _arguments(config)
    arguments[arguments.index("--detection-threshold") + 1] = "nan"
    assert cli.main(arguments) == 2
    assert not config.output.exists()


def test_main_returns_two_and_creates_no_output_when_model_initialization_is_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg")
    labels = _write_labels(photos, [("face.jpg", "rider-a", "yes")])
    config = _config(tmp_path, photos, labels, tmp_path / "run")

    def fail_initialization(*args: object, **kwargs: object) -> object:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(cli, "YuNetDetector", fail_initialization)

    assert cli.main(_arguments(config)) == 2
    assert not config.output.exists()


def _arguments(config: cli.RunConfig) -> list[str]:
    return [
        "run",
        "--photos",
        str(config.photos),
        "--labels",
        str(config.labels),
        "--yunet-model",
        str(config.yunet_model),
        "--sface-model",
        str(config.sface_model),
        "--output",
        str(config.output),
        "--detection-threshold",
        str(config.detection_threshold),
        "--min-face-px",
        str(config.min_face_px),
        "--top-k",
        str(config.top_k),
        "--max-image-dimension",
        str(config.max_image_dimension),
        "--max-image-pixels",
        str(config.max_image_pixels),
    ]
