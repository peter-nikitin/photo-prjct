from __future__ import annotations

import gc
import weakref
from pathlib import Path

import numpy as np
from face_spike.analysis import (
    BoundingBox,
    DecodedImage,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    FaceProcessingError,
    analyze_event_photo_inventory,
)
from face_spike.inventory import EventPhoto, EventPhotoInventory


def _inventory(*filenames: str) -> EventPhotoInventory:
    root = Path("event")
    return EventPhotoInventory(
        tuple(EventPhoto(filename, root / filename) for filename in filenames)
    )


def _detection(x: float, width: float, confidence: float = 0.9) -> FaceDetection:
    return FaceDetection(
        BoundingBox(x, 3, width, 20),
        FaceLandmarks((x, 3), (x + 1, 3), (x + 1, 4), (x, 5), (x + 1, 5)),
        confidence,
    )


class _Decoder:
    def decode(self, photo: EventPhoto) -> DecodedImage:
        rgb = np.zeros((20, 40, 3), dtype=np.uint8)
        return DecodedImage(rgb, rgb[:, :, ::-1].copy(), width=40, height=20)


class _Detector:
    def __init__(self, detections: tuple[FaceDetection, ...]) -> None:
        self.detections = detections

    def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
        return self.detections


def test_analysis_embeds_all_faces_with_stable_ids_in_normalized_order() -> None:
    calls: list[float] = []

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            calls.append(detection.bounding_box.x)
            return FaceEmbedding(np.asarray([1.0, 0.0], dtype=np.float32))

    analyses = analyze_event_photo_inventory(
        _inventory("frame.jpg"),
        _Decoder(),
        _Detector((_detection(20, 20), _detection(2, 10))),
        Recognizer(),
    )

    assert calls == [2, 20]
    assert [
        (face.face_id, face.filename, face.face_index, face.crop_path) for face in analyses[0].faces
    ] == [
        (
            "frame.jpg#face-001",
            "frame.jpg",
            1,
            "faces/a98f9987d491cd06bde0e815b7ab29cd62bcfc1142adf7be8da67e4b31020bcb.png",
        ),
        (
            "frame.jpg#face-002",
            "frame.jpg",
            2,
            "faces/73e3baf01364e3a8b6b4b64d07624137d3aee750b0ddd696b6070d74141442d9.png",
        ),
    ]
    assert [face.status for face in analyses[0].faces] == ["ok", "ok"]


def test_analysis_retains_successful_faces_when_another_embedding_fails() -> None:
    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            if detection.bounding_box.x == 1:
                raise FaceProcessingError("alignment_failed")
            return FaceEmbedding(np.asarray([0.0, 1.0], dtype=np.float32))

    analyses = analyze_event_photo_inventory(
        _inventory("frame.jpg"),
        _Decoder(),
        _Detector((_detection(1, 10), _detection(20, 20))),
        Recognizer(),
    )

    assert analyses[0].status == "ok"
    assert [(face.status, face.embedding is not None) for face in analyses[0].faces] == [
        ("alignment_failed", False),
        ("ok", True),
    ]


def test_analysis_releases_decoded_arrays_before_loading_the_next_photo() -> None:
    references: list[weakref.ReferenceType[np.ndarray]] = []

    class ReleasingDecoder:
        def decode(self, photo: EventPhoto) -> DecodedImage:
            assert all(reference() is None for reference in references)
            rgb = np.zeros((20, 40, 3), dtype=np.uint8)
            bgr = rgb[:, :, ::-1].copy()
            references.extend((weakref.ref(rgb), weakref.ref(bgr)))
            return DecodedImage(rgb, bgr, width=40, height=20)

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            return FaceEmbedding(np.asarray([1.0], dtype=np.float32))

    analyses = analyze_event_photo_inventory(
        _inventory("alpha.jpg", "bravo.jpg"),
        ReleasingDecoder(),
        _Detector((_detection(1, 10),)),
        Recognizer(),
    )

    assert [analysis.filename for analysis in analyses] == ["alpha.jpg", "bravo.jpg"]
    gc.collect()
    assert all(reference() is None for reference in references)
