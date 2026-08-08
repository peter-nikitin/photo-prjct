from __future__ import annotations

import gc
import weakref
from pathlib import Path

import numpy as np
import pytest
from face_spike.analysis import (
    BoundingBox,
    DecodedImage,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    face_crop_path,
)
from face_spike.index import SourceFaceRecord, build_face_index
from face_spike.index_artifacts import FaceIndexManifest
from face_spike.inventory import EventPhoto, EventPhotoInventory
from face_spike.quality import FaceQualityThresholds


def _thresholds(*, minimum_face_px: int = 1) -> FaceQualityThresholds:
    return FaceQualityThresholds(
        "normalized-laplacian-v1",
        112,
        minimum_face_px,
        0.0,
        1.0,
        0.0,
        0.0,
    )


def _source_face(
    filename: str,
    face_index: int,
    x: float,
    *,
    status: str = "ok",
) -> SourceFaceRecord:
    return SourceFaceRecord(
        face_id=(face_id := f"{filename}#face-{face_index:03d}"),
        filename=filename,
        face_index=face_index,
        bounding_box=BoundingBox(x, 2.0, 10.0, 12.0),
        crop_path=face_crop_path(face_id),
        status=status,
    )


def _inventory(*filenames: str) -> EventPhotoInventory:
    return EventPhotoInventory(
        tuple(EventPhoto(filename, Path("photos") / filename) for filename in filenames)
    )


def _manifest() -> FaceIndexManifest:
    return FaceIndexManifest(
        source_run_manifest_sha256="a" * 64,
        source_faces_sha256="b" * 64,
        yunet_model={"basename": "yunet.onnx", "size": 1, "sha256": "c" * 64},
        sface_model={"basename": "sface.onnx", "size": 2, "sha256": "d" * 64},
        parameters={"minimum_face_px": 10},
        dependency_versions={"numpy": "2.2.6"},
        entry_count=0,
        embedding_dimension=0,
        created_at="2026-07-28T10:00:00Z",
    )


def _detection(x: float) -> FaceDetection:
    return FaceDetection(
        BoundingBox(x, 2.0, 10.0, 12.0),
        FaceLandmarks((x + 2, 4), (x + 8, 4), (x + 5, 6), (x + 2, 10), (x + 8, 10)),
        0.9,
    )


class _Decoder:
    def decode(self, photo: EventPhoto) -> DecodedImage:
        rgb = np.full((30, 40, 3), 128, dtype=np.uint8)
        return DecodedImage(rgb, rgb[:, :, ::-1].copy(), 40, 30)


class _Detector:
    def __init__(self, by_filename: dict[str, tuple[FaceDetection, ...]]) -> None:
        self.by_filename = by_filename
        self.current_filename = ""

    def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
        return self.by_filename[self.current_filename]


class _Recognizer:
    def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
        return FaceEmbedding(np.asarray([detection.bounding_box.x, 1.0], dtype=np.float32))


def _build(
    source_faces: tuple[SourceFaceRecord, ...],
    inventory: EventPhotoInventory,
    detector: _Detector,
) -> object:
    class Decoder(_Decoder):
        def decode(self, photo: EventPhoto) -> DecodedImage:
            detector.current_filename = photo.filename
            return super().decode(photo)

    return build_face_index(
        source_faces,
        inventory,
        Decoder(),
        detector,
        _Recognizer(),
        quality_thresholds=_thresholds(),
        manifest=_manifest(),
    )


def test_builder_binds_normalized_vectors_to_source_faces_in_face_id_order() -> None:
    alpha = _source_face("alpha.jpg", 1, 3)
    bravo = _source_face("bravo.jpg", 1, 7)
    index = _build(
        (bravo, alpha),
        _inventory("bravo.jpg", "alpha.jpg"),
        _Detector({"alpha.jpg": (_detection(3),), "bravo.jpg": (_detection(7),)}),
    )

    assert [entry.face_id for entry in index.entries] == [alpha.face_id, bravo.face_id]
    assert [(entry.filename, entry.face_index, entry.bounding_box) for entry in index.entries] == [
        (alpha.filename, alpha.face_index, alpha.bounding_box),
        (bravo.filename, bravo.face_index, bravo.bounding_box),
    ]
    assert index.embeddings.dtype == np.float32
    assert index.embeddings.flags.c_contiguous
    assert np.allclose(np.linalg.norm(index.embeddings, axis=1), 1.0)
    assert index.manifest == _manifest()


@pytest.mark.parametrize(
    "source_faces,detections,match",
    [
        ((_source_face("frame.jpg", 1, 2),), (), "missing required source face"),
        ((), (_detection(2),), "unexpected detected face"),
        ((_source_face("frame.jpg", 1, 3),), (_detection(2),), "source geometry differs"),
        (
            (_source_face("frame.jpg", 1, 2), _source_face("frame.jpg", 1, 2)),
            (_detection(2),),
            "duplicate source face ID",
        ),
    ],
)
def test_builder_rejects_any_reconciliation_that_can_misbind_a_vector(
    source_faces: tuple[SourceFaceRecord, ...],
    detections: tuple[FaceDetection, ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _build(source_faces, _inventory("frame.jpg"), _Detector({"frame.jpg": detections}))


def test_builder_allows_recoverable_failure_for_face_not_required_by_source() -> None:
    source = _source_face("frame.jpg", 1, 2)

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            if detection.bounding_box.x == 20:
                raise RuntimeError("recoverable")
            return FaceEmbedding(np.asarray([1.0, 0.0], dtype=np.float32))

    detector = _Detector({"frame.jpg": (_detection(2), _detection(20))})

    class Decoder(_Decoder):
        def decode(self, photo: EventPhoto) -> DecodedImage:
            detector.current_filename = photo.filename
            return super().decode(photo)

    index = build_face_index(
        (source,),
        _inventory("frame.jpg"),
        Decoder(),
        detector,
        Recognizer(),
        quality_thresholds=_thresholds(),
        manifest=_manifest(),
    )

    assert [entry.face_id for entry in index.entries] == [source.face_id]


@pytest.mark.parametrize(
    "failed_source",
    [
        SourceFaceRecord(
            face_id="failed.jpg#face-001",
            filename="failed.jpg",
            face_index=1,
            bounding_box=BoundingBox(2.0, 2.0, 10.0, 12.0),
            crop_path="../not-a-crop.png",
            status="alignment_failed",
        ),
        SourceFaceRecord(
            face_id="frame.jpg#face-001",
            filename="frame.jpg",
            face_index=1,
            bounding_box=BoundingBox(2.0, 2.0, 10.0, 12.0),
            crop_path="../duplicate-id-is-ignored.png",
            status="embedding_failed",
        ),
    ],
)
def test_builder_ignores_invalid_nonrequired_source_rows(failed_source: SourceFaceRecord) -> None:
    source = _source_face("frame.jpg", 1, 2)
    index = _build(
        (source, failed_source),
        _inventory("frame.jpg"),
        _Detector({"frame.jpg": (_detection(2),)}),
    )

    assert [entry.face_id for entry in index.entries] == [source.face_id]


def test_builder_releases_decoded_pixels_before_next_photo() -> None:
    references: list[weakref.ReferenceType[np.ndarray]] = []
    source_faces = (_source_face("alpha.jpg", 1, 2), _source_face("bravo.jpg", 1, 3))
    detector = _Detector({"alpha.jpg": (_detection(2),), "bravo.jpg": (_detection(3),)})

    class Decoder:
        def decode(self, photo: EventPhoto) -> DecodedImage:
            assert all(reference() is None for reference in references)
            detector.current_filename = photo.filename
            rgb = np.zeros((30, 40, 3), dtype=np.uint8)
            bgr = rgb[:, :, ::-1].copy()
            references.extend((weakref.ref(rgb), weakref.ref(bgr)))
            return DecodedImage(rgb, bgr, 40, 30)

    build_face_index(
        source_faces,
        _inventory("alpha.jpg", "bravo.jpg"),
        Decoder(),
        detector,
        _Recognizer(),
        quality_thresholds=_thresholds(),
        manifest=_manifest(),
    )

    gc.collect()
    assert all(reference() is None for reference in references)
