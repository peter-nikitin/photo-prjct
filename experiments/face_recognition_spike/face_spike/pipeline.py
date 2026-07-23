from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from face_spike.domain import ErrorCode, LoadedImage


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True)
class FaceLandmarks:
    right_eye: tuple[float, float]
    left_eye: tuple[float, float]
    nose: tuple[float, float]
    right_mouth_corner: tuple[float, float]
    left_mouth_corner: tuple[float, float]

    def as_yunet_values(self) -> tuple[float, ...]:
        return (
            *self.right_eye,
            *self.left_eye,
            *self.nose,
            *self.right_mouth_corner,
            *self.left_mouth_corner,
        )


@dataclass(frozen=True)
class FaceDetection:
    bounding_box: BoundingBox
    landmarks: FaceLandmarks
    confidence: float
    primary: bool = False

    def as_yunet_row(self) -> NDArray[np.float32]:
        box = self.bounding_box
        return np.asarray(
            [
                box.x,
                box.y,
                box.width,
                box.height,
                *self.landmarks.as_yunet_values(),
                self.confidence,
            ],
            dtype=np.float32,
        ).reshape(1, 15)


@dataclass(frozen=True, eq=False)
class FaceEmbedding:
    vector: NDArray[np.float32]

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FaceEmbedding) and np.array_equal(self.vector, other.vector)


AnalysisStatus = (
    Literal[
        "ok",
        "no_detection",
        "detection_failed",
        "alignment_failed",
        "embedding_failed",
        "invalid_embedding",
    ]
    | ErrorCode
)
ProcessingErrorCode = Literal[
    "detection_failed",
    "alignment_failed",
    "embedding_failed",
    "invalid_embedding",
]


class ModelProcessingError(Exception):
    def __init__(self, code: str) -> None:
        if code not in _PROCESSING_ERROR_CODES:
            raise ValueError("invalid model processing error code")
        self.code = cast(ProcessingErrorCode, code)
        super().__init__(code)


_PROCESSING_ERROR_CODES: frozenset[str] = frozenset(
    {"detection_failed", "alignment_failed", "embedding_failed", "invalid_embedding"}
)


@dataclass(frozen=True)
class ImageAnalysis:
    image: LoadedImage
    detections: tuple[FaceDetection, ...]
    primary_detection: FaceDetection | None
    embedding: FaceEmbedding | None
    status: AnalysisStatus


class FaceDetector(Protocol):
    def detect(self, bgr: NDArray[np.uint8]) -> tuple[FaceDetection, ...]: ...


class FaceRecognizer(Protocol):
    def extract(self, bgr: NDArray[np.uint8], detection: FaceDetection) -> FaceEmbedding: ...


def analyze_image(
    image: LoadedImage,
    detector: FaceDetector,
    recognizer: FaceRecognizer,
    min_face_px: int = 1,
) -> ImageAnalysis:
    if min_face_px < 1:
        raise ValueError("min_face_px must be positive")

    try:
        detected = detector.detect(image.bgr)
    except Exception:
        return ImageAnalysis(image, (), None, None, "detection_failed")

    detections = tuple(
        detection
        for detection in detected
        if detection.bounding_box.width >= min_face_px
        and detection.bounding_box.height >= min_face_px
    )
    if not detections:
        return ImageAnalysis(image, (), None, None, "no_detection")

    detections, primary = _mark_primary(detections)
    try:
        embedding = recognizer.extract(image.bgr, primary)
    except ModelProcessingError as error:
        status = error.code if error.code in _PROCESSING_ERROR_CODES else "embedding_failed"
        return ImageAnalysis(image, detections, primary, None, cast(ProcessingErrorCode, status))
    except Exception:
        return ImageAnalysis(image, detections, primary, None, "embedding_failed")
    return ImageAnalysis(image, detections, primary, embedding, "ok")


def _mark_primary(
    detections: tuple[FaceDetection, ...],
) -> tuple[tuple[FaceDetection, ...], FaceDetection]:
    primary_index = min(
        range(len(detections)),
        key=lambda index: _primary_sort_key(detections[index]),
    )
    normalized = tuple(
        replace(detection, primary=index == primary_index)
        for index, detection in enumerate(detections)
    )
    return normalized, normalized[primary_index]


def _primary_sort_key(detection: FaceDetection) -> tuple[float, float, float, float, float, float]:
    box = detection.bounding_box
    return (-box.area, -detection.confidence, box.x, box.y, box.width, box.height)
