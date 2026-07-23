from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import face_spike.artifacts as artifacts
import numpy as np
import pytest
from face_spike.artifacts import ExperimentResult, asset_relative_path, write_run
from face_spike.cli import RunConfig
from face_spike.domain import DatasetItem, FaceExpected, LoadedImage
from face_spike.pipeline import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    ImageAnalysis,
)
from face_spike.retrieval import evaluate_retrieval
from PIL import Image


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


def _analysis(
    filename: str,
    group: str | None,
    vector: tuple[float, float] | None,
    *,
    expected: FaceExpected = FaceExpected.YES,
    status: str = "ok",
    primary: bool = True,
    size: tuple[int, int] = (48, 36),
) -> ImageAnalysis:
    width, height = size
    rgb = np.full((height, width, 3), (30, 60, 90), dtype=np.uint8)
    image = LoadedImage(
        DatasetItem(filename, group, expected), rgb, rgb[:, :, ::-1].copy(), width, height
    )
    detection = FaceDetection(
        BoundingBox(2, 3, 20, 16),
        FaceLandmarks((4, 5), (16, 5), (10, 10), (5, 15), (15, 15)),
        0.875,
        primary=primary,
    )
    return ImageAnalysis(
        image=image,
        detections=(detection,) if primary else (),
        primary_detection=detection if primary else None,
        embedding=(FaceEmbedding(np.asarray(vector, dtype=np.float32)) if vector else None),
        status=status,  # type: ignore[arg-type]
    )


def _result(tmp_path: Path, *, timestamp: datetime | None = None) -> ExperimentResult:
    analyses = (
        _analysis("z-last.jpg", "rider-b", (0.0, 1.0)),
        _analysis("a-first.jpg", "rider-a", (1.0, 0.0)),
        _analysis(
            "failure.jpg",
            None,
            None,
            expected=FaceExpected.NO,
            status="detection_failed",
            primary=False,
        ),
    )
    started = timestamp or datetime(2026, 7, 22, 10, tzinfo=UTC)
    return ExperimentResult(
        config=_config(tmp_path),
        analyses=analyses,
        retrieval=evaluate_retrieval(analyses, top_k=10),
        started_at=started,
        finished_at=started + timedelta(seconds=2),
        durations={"decode": 0.5, "detection": 1.0, "embedding": 0.5},
        unexpected_labelled_image_failures=1,
        dependency_versions={"numpy": "2.2.6", "opencv": "4.12.0.88", "pillow": "12.0.0"},
    )


def test_write_run_creates_exclusive_complete_artifact_tree(tmp_path: Path) -> None:
    output = tmp_path / "run"

    write_run(output, _result(tmp_path))

    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "detections.csv",
        "retrieval.csv",
        "metrics.json",
        "annotated",
        "crops",
        "report.html",
    }
    assert (output / "annotated" / asset_relative_path("a-first.jpg", ".jpg")).is_file()
    assert (output / "crops" / asset_relative_path("a-first.jpg", ".png")).is_file()


def test_write_run_refuses_existing_path_without_modifying_it(tmp_path: Path) -> None:
    output = tmp_path / "run"
    output.mkdir()
    sentinel = output / "preserve.txt"
    sentinel.write_text("immutable", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_run(output, _result(tmp_path))

    assert sentinel.read_text(encoding="utf-8") == "immutable"
    assert list(output.iterdir()) == [sentinel]


def test_artifact_records_are_sorted_atomic_and_do_not_serialize_embeddings(tmp_path: Path) -> None:
    output = tmp_path / "run"
    result = _result(tmp_path)

    write_run(output, result)

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    with (output / "detections.csv").open(newline="", encoding="utf-8") as stream:
        detection_rows = list(csv.DictReader(stream))
    with (output / "retrieval.csv").open(newline="", encoding="utf-8") as stream:
        retrieval_rows = list(csv.DictReader(stream))

    assert list(detection_rows[0]) == [
        "filename",
        "participant_group",
        "face_expected",
        "detection_count",
        "primary_x",
        "primary_y",
        "primary_width",
        "primary_height",
        "primary_confidence",
        "crop_width",
        "crop_height",
        "status",
        "error_code",
    ]
    assert list(retrieval_rows[0]) == [
        "query_filename",
        "candidate_filename",
        "rank",
        "cosine_distance",
        "same_group",
        "query_participant_group",
        "candidate_participant_group",
    ]
    assert [row["filename"] for row in detection_rows] == [
        "a-first.jpg",
        "failure.jpg",
        "z-last.jpg",
    ]
    assert manifest["model_hashes"] == {
        "sface": "8eb0eec27532fb9d53788b23b9dbdf75a58a393dca77acaff31b6fe7f378f68d",
        "yunet": "aa3c8a9bb822777b91169470e1f6a0c3b61d55886668a7ea9bde4933d16cd7b4",
    }
    assert manifest["dependency_versions"] == {
        "numpy": "2.2.6",
        "opencv": "4.12.0.88",
        "pillow": "12.0.0",
    }
    assert manifest["started_at"] == "2026-07-22T10:00:00Z"
    assert manifest["finished_at"] == "2026-07-22T10:00:02Z"
    assert manifest["duration_seconds"] == 2.0
    assert manifest["durations_seconds"] == {
        "decode": 0.5,
        "detection": 1.0,
        "embedding": 0.5,
    }
    assert manifest["counts"] == {
        "accepted_detections": 2,
        "dataset_images": 3,
        "embedding_success": 2,
        "face_expected": {"no": 1, "uncertain": 0, "yes": 2},
        "primary_detections": 2,
        "unexpected_labelled_image_failures": 1,
    }
    assert manifest["error_counts"] == {"detection_failed": 1}
    assert metrics["latency_seconds"] == {
        "decode": 0.5,
        "detection": 1.0,
        "embedding": 0.5,
    }
    assert metrics["error_counts"] == {"detection_failed": 1}
    assert metrics["retrieval"]["recall_at_1"] is None
    assert set(manifest["parameters"]) == {
        "detection_threshold",
        "input_photos_basename",
        "labels_filename",
        "max_image_dimension",
        "max_image_pixels",
        "min_face_px",
        "sface_model_filename",
        "top_k",
        "yunet_model_filename",
    }
    rendered = "\n".join(
        (output / name).read_text(encoding="utf-8")
        for name in ("manifest.json", "metrics.json", "report.html")
    )
    assert "1.0, 0.0" not in rendered
    assert "vector" not in rendered
    assert str(tmp_path) not in rendered
    assert not list(output.rglob(".*.tmp"))


def test_annotated_preview_is_bounded_and_primary_crop_is_lossless_png(tmp_path: Path) -> None:
    output = tmp_path / "run"
    result = _result(tmp_path)
    large = _analysis("large.jpg", "rider-a", (1.0, 0.0), size=(3000, 2000))
    result = ExperimentResult(
        **{**result.__dict__, "analyses": (large,), "retrieval": evaluate_retrieval((large,), 10)}
    )

    write_run(output, result)

    with Image.open(output / "annotated" / asset_relative_path("large.jpg", ".jpg")) as preview:
        assert preview.format == "JPEG"
        assert preview.width <= 1920
        assert preview.height <= 1920
    with Image.open(output / "crops" / asset_relative_path("large.jpg", ".png")) as crop:
        assert crop.format == "PNG"
        assert crop.size == (20, 16)


def test_equivalent_results_have_stable_output_after_timestamp_normalization(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_run(first, _result(tmp_path / "one", timestamp=datetime(2026, 7, 22, 10, tzinfo=UTC)))
    write_run(second, _result(tmp_path / "two", timestamp=datetime(2026, 7, 23, 12, tzinfo=UTC)))

    def normalized(path: Path) -> str:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in ("started_at", "finished_at"):
            payload.pop(key, None)
        return json.dumps(payload, sort_keys=True)

    for name in ("manifest.json", "metrics.json"):
        assert normalized(first / name) == normalized(second / name)
    assert (first / "detections.csv").read_bytes() == (second / "detections.csv").read_bytes()
    assert (first / "retrieval.csv").read_bytes() == (second / "retrieval.csv").read_bytes()
    assert (first / "report.html").read_bytes() == (second / "report.html").read_bytes()
    for filename in ("a-first.jpg", "z-last.jpg"):
        for directory, suffix in (("annotated", ".jpg"), ("crops", ".png")):
            relative = asset_relative_path(filename, suffix)
            assert (first / directory / relative).read_bytes() == (
                second / directory / relative
            ).read_bytes()


def test_asset_paths_are_contained_collision_safe_and_report_uses_same_mapping(
    tmp_path: Path,
) -> None:
    filenames = ("face.jpg", "face.jpeg", "nested/face.jpg", "/absolute/face.jpg", "../escape.jpg")
    analyses = tuple(_analysis(filename, "rider", (1.0, 0.0)) for filename in filenames)
    result = _result(tmp_path)
    result = ExperimentResult(
        **{**result.__dict__, "analyses": analyses, "retrieval": evaluate_retrieval(analyses, 10)}
    )
    output = tmp_path / "run"

    write_run(output, result)

    preview_paths = [asset_relative_path(filename, ".jpg") for filename in filenames]
    crop_paths = [asset_relative_path(filename, ".png") for filename in filenames]
    assert len(set(preview_paths)) == len(filenames)
    assert len(set(crop_paths)) == len(filenames)
    for relative in (*preview_paths, *crop_paths):
        assert not relative.is_absolute()
        assert ".." not in relative.parts
    assert {
        (path.relative_to(output / "annotated")) for path in (output / "annotated").iterdir()
    } == set(preview_paths)
    assert {(path.relative_to(output / "crops")) for path in (output / "crops").iterdir()} == set(
        crop_paths
    )
    report = (output / "report.html").read_text(encoding="utf-8")
    for relative in preview_paths:
        assert f"annotated/{relative.as_posix()}" in report
    assert not (tmp_path / "escape.jpg").exists()


def test_write_run_removes_partial_output_after_a_failure_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "run"
    result = _result(tmp_path)
    original = artifacts._write_diagnostic_images

    def fail_after_first_image(*args: object) -> None:
        original(*args)  # type: ignore[arg-type]
        raise RuntimeError("injected image publication failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(artifacts, "_write_diagnostic_images", fail_after_first_image)
        with pytest.raises(RuntimeError, match="injected image publication failure"):
            write_run(output, result)

    assert not output.exists()
    write_run(output, result)
    assert (output / "report.html").is_file()


def test_atomic_publication_uses_same_directory_temporary_files_and_cleans_up_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "run"
    replaced: list[tuple[Path, Path]] = []
    original_replace = artifacts.os.replace

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        replaced.append((Path(source), Path(destination)))
        raise OSError("injected replace failure")

    monkeypatch.setattr(artifacts.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        write_run(output, _result(tmp_path))

    assert replaced
    assert all(source.parent == destination.parent for source, destination in replaced)
    assert not output.exists()
    monkeypatch.setattr(artifacts.os, "replace", original_replace)


@pytest.mark.parametrize(
    ("size", "expected_size"),
    [((1, 12000), (1, 1920)), ((12000, 1), (1920, 1))],
)
def test_extreme_aspect_ratio_preview_dimensions_remain_at_least_one_pixel(
    tmp_path: Path, size: tuple[int, int], expected_size: tuple[int, int]
) -> None:
    analysis = _analysis("thin.jpg", "rider", (1.0, 0.0), size=size)
    result = _result(tmp_path)
    result = ExperimentResult(
        **{
            **result.__dict__,
            "analyses": (analysis,),
            "retrieval": evaluate_retrieval((analysis,), 10),
        }
    )
    output = tmp_path / "run"

    write_run(output, result)

    with Image.open(output / "annotated" / asset_relative_path("thin.jpg", ".jpg")) as preview:
        assert preview.size == expected_size


def test_metrics_and_detection_evidence_preserve_bucket_boundaries_and_clipped_crop(
    tmp_path: Path,
) -> None:
    base = _analysis("boundaries.jpg", "rider", (1.0, 0.0))
    landmarks = FaceLandmarks((4, 5), (16, 5), (10, 10), (5, 15), (15, 15))
    detections = (
        FaceDetection(BoundingBox(-4.2, -1.2, 31, 31), landmarks, 0.99, primary=True),
        FaceDetection(BoundingBox(2, 3, 32, 32), landmarks, 0.88),
        FaceDetection(BoundingBox(2, 3, 64, 64), landmarks, 0.77),
        FaceDetection(BoundingBox(2, 3, 128, 128), landmarks, 0.66),
        FaceDetection(BoundingBox(2, 3, 256, 256), landmarks, 0.55),
    )
    analysis = ImageAnalysis(
        base.image,
        detections,
        detections[0],
        base.embedding,
        base.status,
    )
    result = _result(tmp_path)
    result = ExperimentResult(
        **{
            **result.__dict__,
            "analyses": (analysis,),
            "retrieval": evaluate_retrieval((analysis,), 10),
        }
    )
    output = tmp_path / "run"

    write_run(output, result)

    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    with (output / "detections.csv").open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    with Image.open(output / "crops" / asset_relative_path("boundaries.jpg", ".png")) as crop:
        assert crop.size == (27, 30)
    assert metrics["face_size_buckets"] == {
        "<32": 1,
        "32-63": 1,
        "64-127": 1,
        "128-255": 1,
        ">=256": 1,
    }
    assert row["detection_count"] == "5"
    assert row["primary_x"] == "-4.2"
    assert row["primary_confidence"] == "0.99"
    assert row["crop_width"] == "27"
    assert row["crop_height"] == "30"


def test_retrieval_csv_has_exactly_ten_stably_ranked_candidates_when_available(
    tmp_path: Path,
) -> None:
    analyses = (
        _analysis("query.jpg", "rider", (1.0, 0.0)),
        *tuple(_analysis(f"candidate-{index:02}.jpg", "rider", (0.0, 1.0)) for index in range(11)),
    )
    result = _result(tmp_path)
    result = ExperimentResult(
        **{**result.__dict__, "analyses": analyses, "retrieval": evaluate_retrieval(analyses, 10)}
    )
    output = tmp_path / "run"

    write_run(output, result)

    with (output / "retrieval.csv").open(newline="", encoding="utf-8") as stream:
        query_rows = [row for row in csv.DictReader(stream) if row["query_filename"] == "query.jpg"]
    assert [row["rank"] for row in query_rows] == [str(rank) for rank in range(1, 11)]
    assert [row["candidate_filename"] for row in query_rows] == [
        f"candidate-{index:02}.jpg" for index in range(10)
    ]


def test_preview_draws_all_detections_with_confidence_and_distinct_primary_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _analysis("diagnostic.jpg", "rider", (1.0, 0.0))
    landmarks = FaceLandmarks((4, 5), (16, 5), (10, 10), (5, 15), (15, 15))
    secondary = FaceDetection(BoundingBox(1.5, 2.5, 10, 12), landmarks, 0.61)
    primary = FaceDetection(BoundingBox(20, 21, 8, 9), landmarks, 0.987, primary=True)
    analysis = ImageAnalysis(base.image, (secondary, primary), primary, base.embedding, "ok")
    result = _result(tmp_path)
    result = ExperimentResult(
        **{
            **result.__dict__,
            "analyses": (analysis,),
            "retrieval": evaluate_retrieval((analysis,), 10),
        }
    )
    rectangles: list[tuple[tuple[float, float, float, float], str, int]] = []
    texts: list[tuple[tuple[float, float], str, str, int, str]] = []
    real_draw = artifacts.ImageDraw.Draw

    class RecordingDraw:
        def __init__(self, image: Image.Image) -> None:
            self.delegate = real_draw(image)

        def rectangle(
            self, coordinates: tuple[float, float, float, float], **kwargs: object
        ) -> None:
            rectangles.append((coordinates, str(kwargs["outline"]), int(kwargs["width"])))
            self.delegate.rectangle(coordinates, **kwargs)

        def text(self, position: tuple[float, float], text: str, **kwargs: object) -> None:
            texts.append(
                (
                    position,
                    text,
                    str(kwargs["fill"]),
                    int(kwargs["stroke_width"]),
                    str(kwargs["stroke_fill"]),
                )
            )
            self.delegate.text(position, text, **kwargs)

    monkeypatch.setattr(artifacts.ImageDraw, "Draw", RecordingDraw)

    write_run(tmp_path / "run", result)

    assert rectangles == [
        ((1.5, 2.5, 11.5, 14.5), "orange", 3),
        ((20, 21, 28, 30), "lime", 3),
    ]
    assert texts == [
        ((1.5, 0), "0.610", "orange", 1, "black"),
        ((20, 7), "0.987 PRIMARY", "lime", 1, "black"),
    ]
