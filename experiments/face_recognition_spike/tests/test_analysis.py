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
    analyze_decoded_event_photo,
    analyze_event_photo_inventory,
)
from face_spike.inventory import EventPhoto, EventPhotoInventory
from face_spike.quality import FaceQualityThresholds


def _quality_thresholds(**overrides: object) -> FaceQualityThresholds:
    values = {
        "algorithm_version": "normalized-laplacian-v1",
        "crop_size": 112,
        "minimum_face_px": 1,
        "severe_blur_threshold": 0.0,
        "borderline_blur_threshold": 1.0,
        "minimum_relative_area": 0.0,
        "minimum_confidence": 0.0,
    }
    values.update(overrides)
    return FaceQualityThresholds(**values)


def _inventory(*filenames: str) -> EventPhotoInventory:
    root = Path("event")
    return EventPhotoInventory(
        tuple(EventPhoto(filename, root / filename) for filename in filenames)
    )


def _detection(x: float, width: float, confidence: float = 0.9) -> FaceDetection:
    return FaceDetection(
        BoundingBox(x, 0, width, 20),
        FaceLandmarks((x, 1), (x + 1, 1), (x + 1, 2), (x, 3), (x + 1, 3)),
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


def test_decoded_photo_analysis_matches_inventory_analysis_contract() -> None:
    photo = EventPhoto("frame.jpg", Path("event/frame.jpg"))
    decoded = _Decoder().decode(photo)
    detector = _Detector((_detection(20, 20), _detection(2, 10)))
    thresholds = _quality_thresholds()

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            return FaceEmbedding(np.asarray([detection.bounding_box.x, 1.0], dtype=np.float32))

    decoded_analysis = analyze_decoded_event_photo(
        photo,
        decoded,
        detector,
        Recognizer(),
        quality_thresholds=thresholds,
    )
    inventory_analysis = analyze_event_photo_inventory(
        EventPhotoInventory((photo,)),
        _Decoder(),
        detector,
        Recognizer(),
        quality_thresholds=thresholds,
    )[0]

    assert [face.face_id for face in decoded_analysis.faces] == [
        face.face_id for face in inventory_analysis.faces
    ]
    assert [face.quality for face in decoded_analysis.faces] == [
        face.quality for face in inventory_analysis.faces
    ]
    assert [face.status for face in decoded_analysis.faces] == [
        face.status for face in inventory_analysis.faces
    ]
    assert [face.embedding for face in decoded_analysis.faces] == [
        face.embedding for face in inventory_analysis.faces
    ]


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


def test_quality_gate_retains_rejected_detection_and_skips_embedding() -> None:
    calls: list[float] = []
    pixels = np.zeros((100, 100, 3), dtype=np.uint8)

    class Decoder:
        def decode(self, photo: EventPhoto) -> DecodedImage:
            return DecodedImage(pixels, pixels.copy(), width=100, height=100)

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            calls.append(detection.bounding_box.x)
            return FaceEmbedding(np.asarray([1.0], dtype=np.float32))

    analyses = analyze_event_photo_inventory(
        _inventory("frame.jpg"),
        Decoder(),
        _Detector((_detection(2, 10, confidence=0.80),)),
        Recognizer(),
        quality_thresholds=_quality_thresholds(
            minimum_face_px=12,
            severe_blur_threshold=1.0,
            borderline_blur_threshold=2.0,
            minimum_relative_area=0.021,
            minimum_confidence=0.82,
        ),
    )

    face = analyses[0].faces[0]
    assert calls == []
    assert face.status == "quality_rejected"
    assert face.embedding is None
    assert face.quality.decision == "quality_rejected"
    assert face.quality.reasons == ("too_small", "severe_blur")
    assert face.quality.minimum_side_px == 10
    assert face.quality.relative_area == 0.02
    assert face.quality.sharpness == 0


def test_quality_gate_accepts_inclusive_boundaries_and_embeds_face() -> None:
    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            return FaceEmbedding(np.asarray([1.0], dtype=np.float32))

    analyses = analyze_event_photo_inventory(
        _inventory("frame.jpg"),
        _Decoder(),
        _Detector((_detection(2, 10, confidence=0.82),)),
        Recognizer(),
        quality_thresholds=_quality_thresholds(
            minimum_face_px=10,
            severe_blur_threshold=0.0,
            borderline_blur_threshold=1.0,
            minimum_relative_area=0.25,
            minimum_confidence=0.82,
        ),
    )

    face = analyses[0].faces[0]
    assert face.status == "ok"
    assert face.embedding is not None
    assert face.quality.decision == "accepted"
    assert face.quality.reasons == ()


def test_analysis_uses_the_production_recall_first_quality_interface(
    monkeypatch,
) -> None:
    import face_spike.quality as experiment_quality
    import photo_worker.face_quality as production_quality

    assert experiment_quality.FaceQualityThresholds is production_quality.FaceQualityThresholds
    assert experiment_quality.FaceQuality is production_quality.FaceQualityEvidence
    monkeypatch.setattr(
        production_quality,
        "_normalized_crop_sharpness",
        lambda *_args, **_kwargs: 60.0,
    )

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            return FaceEmbedding(np.asarray([1.0], dtype=np.float32))

    analyses = analyze_event_photo_inventory(
        _inventory("frame.jpg"),
        _Decoder(),
        _Detector((_detection(2, 10, confidence=0.80),)),
        Recognizer(),
        quality_thresholds=_quality_thresholds(
            minimum_confidence=0.82,
            minimum_face_px=1,
            minimum_relative_area=0.0,
            severe_blur_threshold=25.0,
            borderline_blur_threshold=50.0,
        ),
    )

    assert analyses[0].faces[0].status == "ok"
    assert analyses[0].faces[0].quality.decision == "accepted"
