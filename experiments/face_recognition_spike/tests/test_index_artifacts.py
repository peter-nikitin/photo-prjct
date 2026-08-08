from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from face_spike.analysis import BoundingBox
from face_spike.index import FaceIndex, FaceIndexEntry
from face_spike.index_artifacts import (
    FaceIndexArtifactWriter,
    FaceIndexManifest,
    face_index_sha256,
    load_face_index,
)
from face_spike.quality import FaceQuality


def _manifest(*, entry_count: int = 2, embedding_dimension: int = 2) -> FaceIndexManifest:
    return FaceIndexManifest(
        source_run_manifest_sha256="a" * 64,
        source_faces_sha256="b" * 64,
        yunet_model={"basename": "yunet.onnx", "size": 1, "sha256": "c" * 64},
        sface_model={"basename": "sface.onnx", "size": 2, "sha256": "d" * 64},
        parameters={"minimum_face_px": 10, "nested": {"confidence": 0.9}},
        dependency_versions={"numpy": "2.2.6", "opencv": "4.12.0"},
        entry_count=entry_count,
        embedding_dimension=embedding_dimension,
        created_at="2026-07-28T10:00:00Z",
    )


def _entry(number: int) -> FaceIndexEntry:
    return FaceIndexEntry(
        face_id=f"frame.jpg#face-{number:03d}",
        filename="frame.jpg",
        face_index=number,
        bounding_box=BoundingBox(float(number), 2.0, 10.0, 12.0),
        crop_path=f"faces/{number}.png",
        quality=FaceQuality(
            "normalized-laplacian-v1",
            112,
            0.9,
            10.0,
            0.1,
            20.0,
            "accepted",
            (),
        ),
    )


def _index() -> FaceIndex:
    return FaceIndex(
        entries=(_entry(1), _entry(2)),
        embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        manifest=_manifest(entry_count=99, embedding_dimension=99),
    )


def test_writer_atomically_publishes_private_float32_index_and_loader_reconciles_it(
    tmp_path: Path,
) -> None:
    output = tmp_path / "index"
    FaceIndexArtifactWriter(output).finish(_index())

    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "faces.json",
        "embeddings.npz",
    }
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["entry_count"] == 2
    assert manifest["embedding_dimension"] == 2
    faces = json.loads((output / "faces.json").read_text(encoding="utf-8"))
    assert [face["face_id"] for face in faces] == ["frame.jpg#face-001", "frame.jpg#face-002"]
    assert all(not Path(face["crop_path"]).is_absolute() for face in faces)
    with np.load(output / "embeddings.npz", allow_pickle=False) as archive:
        assert archive.files == ["embeddings"]
        assert archive["embeddings"].dtype == np.float32
        assert archive["embeddings"].flags.c_contiguous
    loaded = load_face_index(output)
    assert loaded.entries == _index().entries
    assert np.array_equal(loaded.embeddings, _index().embeddings)
    assert loaded.manifest.entry_count == 2
    assert not list(tmp_path.glob(".index.*"))


def test_writer_rejects_existing_destination_without_touching_it(tmp_path: Path) -> None:
    output = tmp_path / "index"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError):
        FaceIndexArtifactWriter(output)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_index_content_hash_includes_embedding_bytes() -> None:
    original = _index()
    changed_embeddings = original.embeddings.copy()
    changed_embeddings[0] = np.asarray([0.8, 0.6], dtype=np.float32)
    changed = FaceIndex(original.entries, changed_embeddings, original.manifest)

    assert face_index_sha256(original) != face_index_sha256(changed)


def test_writer_cleans_hidden_staging_after_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "index"
    writer = FaceIndexArtifactWriter(output)
    from face_spike.index_artifacts import os as artifact_os

    real_replace = artifact_os.replace

    def fail_publish(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("injected publication failure")
        real_replace(source, destination)

    monkeypatch.setattr("face_spike.index_artifacts.os.replace", fail_publish)
    with pytest.raises(OSError, match="injected publication failure"):
        writer.finish(_index())

    assert not output.exists()
    assert not list(tmp_path.glob(".index.*"))


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda root: (root / "manifest.json").write_text("{}", encoding="utf-8"), "schema"),
        (
            lambda root: (root / "faces.json").write_text("[]", encoding="utf-8"),
            "entry count",
        ),
        (
            lambda root: np.savez(
                root / "embeddings.npz", embeddings=np.asarray([[1.0, 0.0]], dtype=np.float32)
            ),
            "entry count",
        ),
        (
            lambda root: np.savez(
                root / "embeddings.npz",
                embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=object),
            ),
            "object",
        ),
        (
            lambda root: np.savez(
                root / "embeddings.npz",
                embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
                extra=np.asarray([1], dtype=np.int64),
            ),
            "exactly embeddings",
        ),
    ],
)
def test_loader_rejects_invalid_schema_or_row_reconciliation(
    tmp_path: Path,
    mutation: object,
    match: str,
) -> None:
    output = tmp_path / "index"
    FaceIndexArtifactWriter(output).finish(_index())
    mutation(output)

    with pytest.raises(ValueError, match=match):
        load_face_index(output)


def test_loader_rejects_duplicate_ids_and_absolute_crop_path(tmp_path: Path) -> None:
    output = tmp_path / "index"
    FaceIndexArtifactWriter(output).finish(_index())
    faces = json.loads((output / "faces.json").read_text(encoding="utf-8"))
    faces[1]["face_id"] = faces[0]["face_id"]
    faces[1]["crop_path"] = "/absolute/face.png"
    (output / "faces.json").write_text(json.dumps(faces), encoding="utf-8")

    with pytest.raises(ValueError, match="face IDs"):
        load_face_index(output)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.__setitem__("source_run_manifest_sha256", "not-a-hash"),
        lambda manifest: manifest.__setitem__("source_faces_sha256", "A" * 64),
        lambda manifest: manifest["yunet_model"].__setitem__("basename", "/private/yunet.onnx"),
        lambda manifest: manifest["sface_model"].__setitem__("basename", "../sface.onnx"),
        lambda manifest: manifest["yunet_model"].__setitem__("basename", "C:yunet.onnx"),
        lambda manifest: manifest["sface_model"].__setitem__("basename", "C:/models/sface.onnx"),
        lambda manifest: manifest["yunet_model"].__setitem__("basename", ""),
        lambda manifest: manifest["yunet_model"].__setitem__("size", -1),
        lambda manifest: manifest["yunet_model"].__setitem__("size", "1"),
        lambda manifest: manifest["yunet_model"].__setitem__("sha256", "not-a-hash"),
        lambda manifest: manifest.__setitem__("parameters", []),
        lambda manifest: manifest.__setitem__("parameters", {"threshold": float("nan")}),
        lambda manifest: manifest.__setitem__("dependency_versions", []),
        lambda manifest: manifest.__setitem__("dependency_versions", {"numpy": 2}),
        lambda manifest: manifest.__setitem__("created_at", "not-a-timestamp"),
        lambda manifest: manifest.__setitem__("created_at", "2026-07-28T10:00:00+00:00"),
    ],
)
def test_loader_rejects_invalid_manifest_values(tmp_path: Path, mutation: object) -> None:
    output = tmp_path / "index"
    FaceIndexArtifactWriter(output).finish(_index())
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    mutation(manifest)
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        load_face_index(output)


@pytest.mark.parametrize("face_ids", [["frame.jpg#face-002", "frame.jpg#face-001"], ["dup", "dup"]])
def test_loader_rejects_out_of_order_or_duplicate_face_ids(
    tmp_path: Path, face_ids: list[str]
) -> None:
    output = tmp_path / "index"
    FaceIndexArtifactWriter(output).finish(_index())
    faces = json.loads((output / "faces.json").read_text(encoding="utf-8"))
    for face, face_id in zip(faces, face_ids, strict=True):
        face["face_id"] = face_id
    (output / "faces.json").write_text(json.dumps(faces), encoding="utf-8")

    with pytest.raises(ValueError, match="face IDs"):
        load_face_index(output)
