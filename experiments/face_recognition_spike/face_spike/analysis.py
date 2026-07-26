from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from .inventory import EventPhoto, EventPhotoInventory

if TYPE_CHECKING:
    from .quality import FaceQuality, FaceQualityThresholds

FaceStatus = Literal[
    "ok",
    "quality_rejected",
    "alignment_failed",
    "embedding_failed",
    "invalid_embedding",
]
ImageStatus = Literal[
    "ok",
    "no_detection",
    "image_decode_failed",
    "unsupported_image",
    "image_too_large",
    "detection_failed",
]
_FACE_FAILURE_STATUSES: frozenset[str] = frozenset(
    {"alignment_failed", "embedding_failed", "invalid_embedding"}
)
_IMAGE_FAILURE_STATUSES: frozenset[str] = frozenset(
    {"image_decode_failed", "unsupported_image", "image_too_large"}
)


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class FaceLandmarks:
    right_eye: tuple[float, float]
    left_eye: tuple[float, float]
    nose: tuple[float, float]
    right_mouth_corner: tuple[float, float]
    left_mouth_corner: tuple[float, float]


@dataclass(frozen=True)
class FaceDetection:
    bounding_box: BoundingBox
    landmarks: FaceLandmarks
    confidence: float


@dataclass(frozen=True, eq=False)
class FaceEmbedding:
    vector: NDArray[np.float32]

    def __post_init__(self) -> None:
        vector = np.asarray(self.vector, dtype=np.float64).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not vector.size or not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
            raise ValueError("invalid embedding")
        object.__setattr__(self, "vector", np.asarray(vector / norm, dtype=np.float32))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FaceEmbedding) and np.array_equal(self.vector, other.vector)


class FaceProcessingError(Exception):
    def __init__(self, code: FaceStatus) -> None:
        if code not in _FACE_FAILURE_STATUSES:
            raise ValueError("invalid face processing error code")
        self.code = code
        super().__init__(code)


class ImageProcessingError(Exception):
    def __init__(self, code: ImageStatus) -> None:
        if code not in _IMAGE_FAILURE_STATUSES:
            raise ValueError("invalid image processing error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DecodedImage:
    rgb: NDArray[np.uint8]
    bgr: NDArray[np.uint8]
    width: int
    height: int


@dataclass(frozen=True)
class FaceInstance:
    face_id: str
    filename: str
    face_index: int
    detection: FaceDetection
    crop_path: str
    status: FaceStatus
    embedding: FaceEmbedding | None
    quality: FaceQuality


@dataclass(frozen=True)
class EventPhotoAnalysis:
    filename: str
    width: int
    height: int
    faces: tuple[FaceInstance, ...]
    status: ImageStatus


class ImageDecoder(Protocol):
    def decode(self, photo: EventPhoto) -> DecodedImage: ...


class FaceDetector(Protocol):
    def detect(self, bgr: NDArray[np.uint8]) -> tuple[FaceDetection, ...]: ...


class FaceRecognizer(Protocol):
    def extract(self, bgr: NDArray[np.uint8], detection: FaceDetection) -> FaceEmbedding: ...


DiagnosticWriter = Callable[[EventPhoto, DecodedImage, EventPhotoAnalysis], None]


def analyze_event_photo_inventory(
    inventory: EventPhotoInventory,
    decoder: ImageDecoder,
    detector: FaceDetector,
    recognizer: FaceRecognizer,
    *,
    min_face_px: int = 1,
    quality_thresholds: FaceQualityThresholds | None = None,
    write_diagnostics: DiagnosticWriter | None = None,
) -> tuple[EventPhotoAnalysis, ...]:
    """Analyze every accepted face and release decoded pixels before the next photo."""
    if min_face_px < 1:
        raise ValueError("min_face_px must be positive")
    from .quality import FaceQualityThresholds

    thresholds = quality_thresholds or FaceQualityThresholds(minimum_face_px=min_face_px)
    thresholds.validate()

    analyses: list[EventPhotoAnalysis] = []
    for photo in inventory.photos:
        try:
            decoded = decoder.decode(photo)
        except ImageProcessingError as error:
            analyses.append(_failed_image_analysis(photo, error.code))
            continue
        except Exception:
            analyses.append(_failed_image_analysis(photo, "image_decode_failed"))
            continue

        try:
            analysis = _analyze_decoded_image(photo, decoded, detector, recognizer, thresholds)
            if write_diagnostics is not None:
                write_diagnostics(photo, decoded, analysis)
        finally:
            del decoded
        analyses.append(analysis)
    return tuple(analyses)


def _analyze_decoded_image(
    photo: EventPhoto,
    decoded: DecodedImage,
    detector: FaceDetector,
    recognizer: FaceRecognizer,
    quality_thresholds: FaceQualityThresholds,
) -> EventPhotoAnalysis:
    try:
        detections = detector.detect(decoded.bgr)
    except Exception:
        return EventPhotoAnalysis(
            photo.filename, decoded.width, decoded.height, (), "detection_failed"
        )

    ordered = tuple(sorted(detections, key=_face_sort_key))
    if not ordered:
        return EventPhotoAnalysis(photo.filename, decoded.width, decoded.height, (), "no_detection")

    faces = tuple(
        _analyze_face(
            photo,
            decoded.bgr,
            detection,
            index,
            recognizer,
            quality_thresholds,
        )
        for index, detection in enumerate(ordered, start=1)
    )
    return EventPhotoAnalysis(photo.filename, decoded.width, decoded.height, faces, "ok")


def _analyze_face(
    photo: EventPhoto,
    bgr: NDArray[np.uint8],
    detection: FaceDetection,
    face_index: int,
    recognizer: FaceRecognizer,
    quality_thresholds: FaceQualityThresholds,
) -> FaceInstance:
    from .quality import evaluate_face_quality

    face_id = f"{photo.filename}#face-{face_index:03d}"
    crop_path = face_crop_path(face_id)
    quality = evaluate_face_quality(bgr, detection, quality_thresholds)
    if quality.decision == "rejected":
        return FaceInstance(
            face_id,
            photo.filename,
            face_index,
            detection,
            crop_path,
            "quality_rejected",
            None,
            quality,
        )
    try:
        embedding = recognizer.extract(bgr, detection)
    except FaceProcessingError as error:
        return FaceInstance(
            face_id, photo.filename, face_index, detection, crop_path, error.code, None, quality
        )
    except ValueError:
        return FaceInstance(
            face_id,
            photo.filename,
            face_index,
            detection,
            crop_path,
            "invalid_embedding",
            None,
            quality,
        )
    except Exception:
        return FaceInstance(
            face_id,
            photo.filename,
            face_index,
            detection,
            crop_path,
            "embedding_failed",
            None,
            quality,
        )
    return FaceInstance(
        face_id, photo.filename, face_index, detection, crop_path, "ok", embedding, quality
    )


def face_crop_path(face_id: str) -> str:
    """Return the deterministic run-relative crop path for one face instance."""
    digest = hashlib.sha256(face_id.encode("utf-8")).hexdigest()
    return f"faces/{digest}.png"


def _failed_image_analysis(photo: EventPhoto, status: ImageStatus) -> EventPhotoAnalysis:
    return EventPhotoAnalysis(photo.filename, 0, 0, (), status)


def _face_sort_key(detection: FaceDetection) -> tuple[float, float, float, float, float]:
    box = detection.bounding_box
    return (box.x, box.y, box.width, box.height, -detection.confidence)
