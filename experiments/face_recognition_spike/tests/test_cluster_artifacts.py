from __future__ import annotations

import csv
import errno
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from face_spike.analysis import (
    BoundingBox,
    DecodedImage,
    EventPhotoAnalysis,
    FaceDetection,
    FaceEmbedding,
    FaceInstance,
    FaceLandmarks,
)
from face_spike.cluster_artifacts import (
    ClusterArtifactWriter,
    ClusterRunResult,
    face_asset_name,
)
from face_spike.clustering import ClusterMember, FaceCluster
from face_spike.inventory import EventPhoto
from face_spike.quality import FaceQuality
from fixtures import make_jpeg


def _detection(x: float) -> FaceDetection:
    return FaceDetection(
        BoundingBox(x, 2, 8, 10),
        FaceLandmarks((x + 2, 4), (x + 6, 4), (x + 4, 6), (x + 2, 9), (x + 6, 9)),
        0.875,
    )


def _face(
    filename: str,
    index: int,
    x: float,
    vector: tuple[float, float] | None = (1.0, 0.0),
    *,
    status: str = "ok",
) -> FaceInstance:
    face_id = f"{filename}#face-{index:03d}"
    return FaceInstance(
        face_id=face_id,
        filename=filename,
        face_index=index,
        detection=_detection(x),
        crop_path=f"faces/{hashlib.sha256(face_id.encode()).hexdigest()}.png",
        status=status,
        embedding=(None if vector is None else FaceEmbedding(np.asarray(vector, dtype=np.float32))),
        quality=FaceQuality(0.875, 8.0, 1 / 12, 100.0, "accepted", ()),
    )


def _analysis(filename: str, *faces: FaceInstance) -> EventPhotoAnalysis:
    return EventPhotoAnalysis(filename, 40, 24, tuple(faces), "ok")


def _run(
    root: Path,
    analyses: tuple[EventPhotoAnalysis, ...],
    clusters: tuple[FaceCluster, ...],
) -> ClusterRunResult:
    yunet = root / "models" / "yunet.onnx"
    sface = root / "models" / "sface.onnx"
    yunet.parent.mkdir(parents=True, exist_ok=True)
    yunet.write_bytes(b"yunet-model")
    sface.write_bytes(b"sface-model")
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    return ClusterRunResult(
        photos=root / "photos",
        yunet_model=yunet,
        sface_model=sface,
        parameters={
            "cluster_threshold": 0.363,
            "detection_threshold": 0.75,
            "distance_block_size": 512,
            "image_limit": None,
            "max_image_dimension": 12000,
            "max_image_pixels": 100_000_000,
            "min_face_px": 32,
            "representative_threshold": 0.363,
        },
        analyses=analyses,
        clusters=clusters,
        started_at=started,
        finished_at=started + timedelta(seconds=2),
        durations={"decode_detection_embedding": 1.5, "clustering": 0.5},
        dependency_versions={"numpy": "2.2.6", "opencv": "4.12.0", "pillow": "12.0.0"},
    )


def _write_diagnostic(
    writer: ClusterArtifactWriter,
    photos: Path,
    analysis: EventPhotoAnalysis,
) -> None:
    pixels = np.full((24, 40, 3), 128, dtype=np.uint8)
    writer.write_diagnostics(
        EventPhoto(analysis.filename, photos / analysis.filename),
        DecodedImage(pixels, pixels[:, :, ::-1].copy(), 40, 24),
        analysis,
    )


def test_cluster_run_publishes_complete_deterministic_artifact_contract(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    group = make_jpeg(photos / "group.jpg", size=(40, 24))
    solo = make_jpeg(photos / "solo.jpg", size=(40, 24))
    group_one = _face("group.jpg", 1, 2)
    group_two = _face("group.jpg", 2, 20, (0.0, 1.0))
    failed = _face("solo.jpg", 1, 4, None, status="alignment_failed")
    analyses = (_analysis("group.jpg", group_one, group_two), _analysis("solo.jpg", failed))
    clusters = (
        FaceCluster("person-0001", group_one.face_id, (ClusterMember(group_one.face_id, 0.0),)),
        FaceCluster("person-0002", group_two.face_id, (ClusterMember(group_two.face_id, 0.0),)),
    )
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    for analysis in analyses:
        _write_diagnostic(writer, photos, analysis)

    writer.finish(_run(tmp_path, analyses, clusters))

    required = {
        "manifest.json",
        "faces.csv",
        "faces.json",
        "clusters.csv",
        "clusters.json",
        "metrics.json",
        "report.html",
    }
    assert required <= {path.name for path in output.iterdir()}
    assert (output / "annotated").is_dir()
    assert (output / "faces").is_dir()
    assert sorted(path.name for path in (output / "faces").iterdir()) == sorted(
        face_asset_name(face.face_id) for face in (group_one, group_two, failed)
    )
    for cluster in clusters:
        person = output / "people" / cluster.cluster_id
        assert person.is_dir()
        assert len(list((person / "faces").iterdir())) == 1
        assert [path.name for path in (person / "photos").iterdir()] == ["group.jpg"]
    assert (output / "people" / "person-0001" / "photos" / "group.jpg").samefile(group)
    assert (output / "people" / "person-0002" / "photos" / "group.jpg").samefile(group)
    assert solo.is_file()

    with (output / "faces.csv").open(newline="", encoding="utf-8") as stream:
        face_rows = list(csv.DictReader(stream))
    with (output / "clusters.csv").open(newline="", encoding="utf-8") as stream:
        cluster_rows = list(csv.DictReader(stream))
    assert list(face_rows[0]) == [
        "face_id",
        "filename",
        "face_index",
        "x",
        "y",
        "width",
        "height",
        "confidence",
        "minimum_side_px",
        "relative_area",
        "sharpness",
        "quality_decision",
        "quality_reasons",
        "status",
        "error_code",
        "crop_path",
    ]
    assert list(cluster_rows[0]) == [
        "cluster_id",
        "representative_face_id",
        "face_id",
        "filename",
        "face_index",
        "distance_to_representative",
    ]
    assert {row["crop_path"] for row in face_rows} == {
        f"faces/{face_asset_name(face.face_id)}" for face in (group_one, group_two, failed)
    }
    faces_payload = json.loads((output / "faces.json").read_text())
    clusters_payload = json.loads((output / "clusters.json").read_text())
    assert faces_payload["images"][0]["faces"][0]["x"] == 2.0
    assert faces_payload["images"][0]["faces"][0]["confidence"] == 0.875
    assert faces_payload["images"][0]["faces"][0]["quality"]["decision"] == "accepted"
    assert faces_payload["images"][0]["faces"][0]["quality"]["reasons"] == []
    assert faces_payload["images"][1]["faces"][0]["status"] == "alignment_failed"
    assert clusters_payload["clusters"][0]["members"][0]["distance_to_representative"] == 0.0
    assert len(clusters_payload["clusters"]) == 2
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["counts"]["face_instances"] == 3
    assert metrics["counts"]["embedding_success"] == 2
    assert metrics["counts"]["quality_accepted"] == 3
    assert metrics["counts"]["quality_rejected"] == 0
    assert metrics["counts"]["clusters"] == 2
    assert metrics["counts"]["singleton_clusters"] == 2
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["photo_materialization"] == {"copy": 0, "hard_link": 2}
    assert manifest["parameters"]["input_photos_basename"] == "photos"
    assert set(manifest["model_hashes"]) == {"sface", "yunet"}

    serialized = "\n".join((output / name).read_text(encoding="utf-8") for name in sorted(required))
    assert str(tmp_path) not in serialized
    assert "embedding" not in (output / "faces.json").read_text(encoding="utf-8").lower()
    assert "PRIMARY" not in serialized
    assert "labels" not in serialized.lower()
    assert "retrieval" not in serialized.lower()
    assert not list(output.parent.glob(f".{output.name}.*"))


def test_source_photo_is_materialized_once_per_cluster_and_copy_fallback_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = tmp_path / "photos"
    source = make_jpeg(photos / "same.jpg", size=(40, 24))
    first = _face("same.jpg", 1, 2)
    second = _face("same.jpg", 2, 20)
    analysis = _analysis("same.jpg", first, second)
    cluster = FaceCluster(
        "person-0001",
        first.face_id,
        (ClusterMember(first.face_id, 0.0), ClusterMember(second.face_id, 0.01)),
    )
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    _write_diagnostic(writer, photos, analysis)

    def unsupported_link(source: Path, destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("face_spike.cluster_artifacts.os.link", unsupported_link)
    writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    materialized = list((output / "people" / "person-0001" / "photos").iterdir())
    assert [path.name for path in materialized] == ["same.jpg"]
    assert materialized[0].read_bytes() == source.read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["photo_materialization"] == {"copy": 1, "hard_link": 0}


@pytest.mark.parametrize(
    "link_errno",
    [errno.EACCES, errno.ENOENT, errno.EIO, errno.EEXIST],
)
def test_unexpected_hard_link_errors_abort_instead_of_copying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_errno: int,
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg", size=(40, 24))
    face = _face("face.jpg", 1, 2)
    analysis = _analysis("face.jpg", face)
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    _write_diagnostic(writer, photos, analysis)

    def unexpected_link_error(source: Path, destination: Path) -> None:
        raise OSError(link_errno, "injected unexpected link error")

    monkeypatch.setattr("face_spike.cluster_artifacts.os.link", unexpected_link_error)
    with pytest.raises(OSError) as error:
        writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    assert error.value.errno == link_errno
    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))


@pytest.mark.parametrize(
    "cluster_id",
    ["../escaped", "person-1", "person-0000", "person-0001/extra"],
)
def test_publisher_rejects_malformed_cluster_ids_before_filesystem_writes(
    tmp_path: Path,
    cluster_id: str,
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg", size=(40, 24))
    face = _face("face.jpg", 1, 2)
    analysis = _analysis("face.jpg", face)
    cluster = FaceCluster(cluster_id, face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)

    with pytest.raises(ValueError, match="cluster IDs"):
        writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    assert not output.exists()
    assert not (tmp_path / "escaped").exists()
    assert not list(tmp_path.glob(".run.*"))


@pytest.mark.parametrize(
    ("filename", "crop_path"),
    [
        ("../outside.jpg", None),
        ("nested/photo.jpg", None),
        ("face.jpg", "../escape.png"),
        ("face.jpg", "/absolute/escape.png"),
        ("face.jpg", "faces/not-the-face-hash.png"),
    ],
)
def test_publisher_rejects_unsafe_filenames_and_inconsistent_crop_paths(
    tmp_path: Path,
    filename: str,
    crop_path: str | None,
) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    source = tmp_path / "outside.jpg" if filename == "../outside.jpg" else photos / filename
    source.parent.mkdir(parents=True, exist_ok=True)
    make_jpeg(source, size=(40, 24))
    face = _face(filename, 1, 2)
    if crop_path is not None:
        face = FaceInstance(
            face.face_id,
            face.filename,
            face.face_index,
            face.detection,
            crop_path,
            face.status,
            face.embedding,
            face.quality,
        )
    analysis = _analysis(filename, face)
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)

    with pytest.raises(ValueError, match="artifact contract"):
        writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    assert not output.exists()
    assert not (tmp_path / "escape.png").exists()
    assert not list(tmp_path.glob(".run.*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX filename semantics")
@pytest.mark.parametrize("filename", [r"..\valid-backslash.jpg", "C:valid-colon.jpg"])
def test_publisher_accepts_current_posix_direct_basenames(
    tmp_path: Path,
    filename: str,
) -> None:
    photos = tmp_path / "photos"
    source = make_jpeg(photos / filename, size=(40, 24))
    face = _face(filename, 1, 2)
    analysis = _analysis(filename, face)
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    _write_diagnostic(writer, photos, analysis)

    writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    materialized = output / "people" / "person-0001" / "photos" / filename
    assert materialized.samefile(source)
    assert not list(tmp_path.glob(".run.*"))


def test_diagnostic_writer_rejects_unsafe_analysis_before_artifact_writes(
    tmp_path: Path,
) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    face = _face("../outside.jpg", 1, 2)
    analysis = _analysis("../outside.jpg", face)
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    pixels = np.full((24, 40, 3), 128, dtype=np.uint8)

    with pytest.raises(ValueError, match="artifact contract"):
        writer.write_diagnostics(
            EventPhoto("../outside.jpg", tmp_path / "outside.jpg"),
            DecodedImage(pixels, pixels[:, :, ::-1].copy(), 40, 24),
            analysis,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))


def test_equivalent_cluster_results_have_identical_serialized_artifacts(tmp_path: Path) -> None:
    snapshots: list[dict[str, bytes]] = []
    for name in ("first", "second"):
        root = tmp_path / name
        photos = root / "photos"
        make_jpeg(photos / "face.jpg", size=(40, 24))
        face = _face("face.jpg", 1, 2)
        analysis = _analysis("face.jpg", face)
        cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
        writer = ClusterArtifactWriter(root / "run", photos)
        _write_diagnostic(writer, photos, analysis)
        writer.finish(_run(root, (analysis,), (cluster,)))
        snapshots.append(
            {
                path.name: path.read_bytes()
                for path in (root / "run").iterdir()
                if path.suffix in {".csv", ".json", ".html"}
            }
        )

    assert snapshots[0] == snapshots[1]


def test_publication_failure_removes_output_and_hidden_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg", size=(40, 24))
    face = _face("face.jpg", 1, 2)
    analysis = _analysis("face.jpg", face)
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    _write_diagnostic(writer, photos, analysis)

    def fail_publish(source: Path, destination: Path) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr("face_spike.cluster_artifacts.os.replace", fail_publish)
    with pytest.raises(OSError, match="injected publication failure"):
        writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))


def test_keyboard_interrupt_during_artifact_writing_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg", size=(40, 24))
    face = _face("face.jpg", 1, 2)
    analysis = _analysis("face.jpg", face)
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    _write_diagnostic(writer, photos, analysis)

    def interrupt_artifact_write(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt("injected artifact cancellation")

    monkeypatch.setattr(
        "face_spike.cluster_artifacts._write_json_atomic",
        interrupt_artifact_write,
    )

    with pytest.raises(KeyboardInterrupt, match="injected artifact cancellation"):
        writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))


def test_cleanup_failure_is_noted_without_masking_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photos = tmp_path / "photos"
    make_jpeg(photos / "face.jpg", size=(40, 24))
    face = _face("face.jpg", 1, 2)
    analysis = _analysis("face.jpg", face)
    cluster = FaceCluster("person-0001", face.face_id, (ClusterMember(face.face_id, 0.0),))
    output = tmp_path / "run"
    writer = ClusterArtifactWriter(output, photos)
    _write_diagnostic(writer, photos, analysis)
    real_abort = writer.abort

    def interrupt_artifact_write(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt("original cancellation")

    def fail_cleanup() -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(
        "face_spike.cluster_artifacts._write_json_atomic",
        interrupt_artifact_write,
    )
    monkeypatch.setattr(writer, "abort", fail_cleanup)

    with pytest.raises(KeyboardInterrupt, match="original cancellation") as error:
        writer.finish(_run(tmp_path, (analysis,), (cluster,)))

    assert any(
        "staging cleanup failed: OSError: injected cleanup failure" in note
        for note in error.value.__notes__
    )
    monkeypatch.setattr(writer, "abort", real_abort)
    real_abort()
    assert not output.exists()
    assert not list(tmp_path.glob(".run.*"))
