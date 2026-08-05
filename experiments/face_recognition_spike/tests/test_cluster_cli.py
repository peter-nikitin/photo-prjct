from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from face_spike import cli, cluster_artifacts
from face_spike.analysis import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
)
from fixtures import make_jpeg


def valid_cluster_arguments(output: Path) -> list[str]:
    return [
        "cluster",
        "--photos",
        "/input/photos",
        "--yunet-model",
        "/models/yunet.onnx",
        "--sface-model",
        "/models/sface.onnx",
        "--output",
        str(output),
    ]


def valid_compare_arguments(output: Path) -> list[str]:
    return [
        "compare",
        "--run",
        "/input/run",
        "--peakshot-export",
        "/input/peakshot",
        "--output",
        str(output),
    ]


def valid_review_arguments(output: Path) -> list[str]:
    return [
        "review",
        "--run",
        "/input/run",
        "--comparison",
        "/input/comparison",
        "--peakshot-export",
        "/input/peakshot",
        "--output",
        str(output),
    ]


@pytest.mark.parametrize("argv", [[], ["unknown"]])
def test_main_returns_invalid_invocation_exit_code(argv: Sequence[str]) -> None:
    assert cli.main(argv) == 2


@pytest.mark.parametrize(
    "missing_argument",
    ["--photos", "--yunet-model", "--sface-model", "--output"],
)
def test_cluster_requires_each_required_argument(tmp_path: Path, missing_argument: str) -> None:
    arguments = valid_cluster_arguments(tmp_path / "run")
    index = arguments.index(missing_argument)
    del arguments[index : index + 2]

    assert cli.main(arguments) == 2


@pytest.mark.parametrize("missing_argument", ["--run", "--peakshot-export", "--output"])
def test_compare_requires_each_required_argument(tmp_path: Path, missing_argument: str) -> None:
    arguments = valid_compare_arguments(tmp_path / "comparison")
    index = arguments.index(missing_argument)
    del arguments[index : index + 2]

    assert cli.main(arguments) == 2


@pytest.mark.parametrize(
    "missing_argument", ["--run", "--comparison", "--peakshot-export", "--output"]
)
def test_review_requires_each_required_argument(tmp_path: Path, missing_argument: str) -> None:
    arguments = valid_review_arguments(tmp_path / "review")
    index = arguments.index(missing_argument)
    del arguments[index : index + 2]

    assert cli.main(arguments) == 2


def test_compare_dispatches_evaluation_only_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    received: list[object] = []
    monkeypatch.setattr(cli, "run_comparison", lambda config: received.append(config))

    assert cli.main(valid_compare_arguments(tmp_path / "comparison")) == 0
    assert received[0].run == Path("/input/run")
    assert received[0].peakshot_export == Path("/input/peakshot")


def test_review_dispatches_immutable_review_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    received: list[object] = []
    monkeypatch.setattr(cli, "run_review", lambda config: received.append(config))

    assert cli.main(valid_review_arguments(tmp_path / "review")) == 0
    assert received[0].run == Path("/input/run")
    assert received[0].comparison == Path("/input/comparison")
    assert received[0].peakshot_export == Path("/input/peakshot")


def test_importing_compare_command_does_not_load_model_or_clustering_modules() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import face_spike.cli; "
            "assert 'face_spike.models' not in sys.modules; "
            "assert 'face_spike.clustering' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_cluster_uses_approved_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    received: list[object] = []

    def record(config: object) -> SimpleNamespace:
        received.append(config)
        return SimpleNamespace()

    monkeypatch.setattr(cli, "run_cluster", record)

    assert cli.main(valid_cluster_arguments(tmp_path / "run")) == 0
    config = received[0]
    assert config.detection_threshold == 0.75
    assert config.min_face_px == 32
    assert config.cluster_threshold == 0.363
    assert config.representative_threshold == 0.363
    assert config.distance_block_size == 512
    assert config.image_limit is None
    assert config.max_image_dimension == 12000
    assert config.max_image_pixels == 100_000_000
    assert config.minimum_quality_confidence == 0.82
    assert config.minimum_relative_face_area == 0.0009
    assert config.minimum_face_sharpness == 50.0


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--detection-threshold", "nan"),
        ("--detection-threshold", "1.01"),
        ("--min-face-px", "0"),
        ("--cluster-threshold", "-0.1"),
        ("--representative-threshold", "2.01"),
        ("--distance-block-size", "0"),
        ("--image-limit", "0"),
        ("--max-image-dimension", "0"),
        ("--max-image-pixels", "0"),
        ("--minimum-quality-confidence", "nan"),
        ("--minimum-quality-confidence", "1.01"),
        ("--minimum-relative-face-area", "-0.1"),
        ("--minimum-relative-face-area", "1.01"),
        ("--minimum-face-sharpness", "-0.1"),
    ],
)
def test_invalid_cluster_configuration_is_rejected_before_orchestration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    option: str,
    value: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "run_cluster",
        lambda config: pytest.fail(f"run_cluster called with {config!r}"),
    )

    assert cli.main([*valid_cluster_arguments(tmp_path / "run"), option, value]) == 2


@pytest.mark.parametrize("kind", ["directory", "dangling_symlink"])
def test_existing_output_is_rejected_before_orchestration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    output = tmp_path / "existing"
    if kind == "directory":
        output.mkdir()
    else:
        output.symlink_to(tmp_path / "missing")
    monkeypatch.setattr(
        cli,
        "run_cluster",
        lambda config: pytest.fail(f"run_cluster called with {config!r}"),
    )

    assert os.path.lexists(output)
    assert cli.main(valid_cluster_arguments(output)) == 2


def test_run_cluster_orchestrates_unlabelled_all_face_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "group.jpg", size=(40, 24))
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")

    class Detector:
        def __init__(self, model_path: Path, *, threshold: float) -> None:
            assert model_path == yunet
            assert threshold == 0.75

        def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
            landmarks = FaceLandmarks((2, 2), (4, 2), (3, 3), (2, 5), (4, 5))
            return (
                FaceDetection(BoundingBox(20, 2, 8, 10), landmarks, 0.9),
                FaceDetection(BoundingBox(2, 2, 8, 10), landmarks, 0.9),
            )

    class Recognizer:
        def __init__(self, model_path: Path) -> None:
            assert model_path == sface

        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            vector = (1.0, 0.0) if detection.bounding_box.x < 10 else (0.0, 1.0)
            return FaceEmbedding(np.asarray(vector, dtype=np.float32))

    monkeypatch.setattr(cli, "YuNetDetector", Detector)
    monkeypatch.setattr(cli, "SFaceRecognizer", Recognizer)
    output = tmp_path / "run"

    result = cli.run_cluster(
        cli.ClusterConfig(
            photos=photos,
            yunet_model=yunet,
            sface_model=sface,
            output=output,
            min_face_px=1,
            cluster_threshold=0.1,
            representative_threshold=0.1,
            minimum_quality_confidence=0.0,
            minimum_relative_face_area=0.0,
            minimum_face_sharpness=0.0,
        )
    )

    assert [face.face_id for face in result.analyses[0].faces] == [
        "group.jpg#face-001",
        "group.jpg#face-002",
    ]
    assert [cluster.cluster_id for cluster in result.clusters] == [
        "person-0001",
        "person-0002",
    ]
    assert (output / "report.html").is_file()
    assert not (output / "retrieval.csv").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(manifest["peak_memory_bytes"], int)
    assert manifest["peak_memory_bytes"] == result.peak_memory_bytes
    assert result.peak_memory_bytes > 0


def test_model_initialization_failure_is_fatal_without_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "photo.jpg")
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    output = tmp_path / "run"

    def fail_initialization(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected model initialization failure")

    monkeypatch.setattr(cli, "YuNetDetector", fail_initialization)

    with pytest.raises(cli.ClusterConfigurationError):
        cli.run_cluster(
            cli.ClusterConfig(
                photos=photos,
                yunet_model=yunet,
                sface_model=sface,
                output=output,
            )
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))


def test_keyboard_interrupt_during_cluster_diagnostics_removes_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "photo.jpg")
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    output = tmp_path / "run"

    class Detector:
        def __init__(self, model_path: Path, *, threshold: float) -> None:
            pass

        def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
            return ()

    class Recognizer:
        def __init__(self, model_path: Path) -> None:
            pass

    def interrupt_diagnostics(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt("injected cancellation")

    monkeypatch.setattr(cli, "YuNetDetector", Detector)
    monkeypatch.setattr(cli, "SFaceRecognizer", Recognizer)
    monkeypatch.setattr(
        cluster_artifacts.ClusterArtifactWriter,
        "write_diagnostics",
        interrupt_diagnostics,
    )

    with pytest.raises(KeyboardInterrupt, match="injected cancellation"):
        cli.run_cluster(
            cli.ClusterConfig(
                photos=photos,
                yunet_model=yunet,
                sface_model=sface,
                output=output,
            )
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))
