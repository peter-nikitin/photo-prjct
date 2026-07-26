from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from .analysis import FaceDetection

QualityDecision = Literal["accepted", "rejected"]
QualityReason = Literal[
    "low_confidence",
    "small_face",
    "small_relative_area",
    "low_sharpness",
]


@dataclass(frozen=True)
class FaceQualityThresholds:
    minimum_confidence: float = 0.0
    minimum_face_px: int = 1
    minimum_relative_area: float = 0.0
    minimum_sharpness: float = 0.0

    def validate(self) -> None:
        if (
            not isfinite(self.minimum_confidence)
            or not 0 <= self.minimum_confidence <= 1
            or self.minimum_face_px < 1
            or not isfinite(self.minimum_relative_area)
            or not 0 <= self.minimum_relative_area <= 1
            or not isfinite(self.minimum_sharpness)
            or self.minimum_sharpness < 0
        ):
            raise ValueError("invalid face quality thresholds")


@dataclass(frozen=True)
class FaceQuality:
    confidence: float
    minimum_side_px: float
    relative_area: float
    sharpness: float
    decision: QualityDecision
    reasons: tuple[QualityReason, ...]


def evaluate_face_quality(
    bgr: NDArray[np.uint8],
    detection: FaceDetection,
    thresholds: FaceQualityThresholds,
) -> FaceQuality:
    thresholds.validate()
    height, width = bgr.shape[:2]
    if width < 1 or height < 1:
        raise ValueError("image dimensions must be positive")
    box = detection.bounding_box
    minimum_side = float(min(box.width, box.height))
    relative_area = float(box.width * box.height / (width * height))
    sharpness = _laplacian_variance(bgr, detection)
    return evaluate_quality_values(
        confidence=float(detection.confidence),
        minimum_side_px=minimum_side,
        relative_area=relative_area,
        sharpness=sharpness,
        thresholds=thresholds,
    )


def evaluate_quality_values(
    *,
    confidence: float,
    minimum_side_px: float,
    relative_area: float,
    sharpness: float,
    thresholds: FaceQualityThresholds,
) -> FaceQuality:
    thresholds.validate()
    reasons: list[QualityReason] = []
    if confidence < thresholds.minimum_confidence:
        reasons.append("low_confidence")
    if minimum_side_px < thresholds.minimum_face_px:
        reasons.append("small_face")
    if relative_area < thresholds.minimum_relative_area:
        reasons.append("small_relative_area")
    if sharpness < thresholds.minimum_sharpness:
        reasons.append("low_sharpness")
    return FaceQuality(
        confidence=float(confidence),
        minimum_side_px=float(minimum_side_px),
        relative_area=relative_area,
        sharpness=sharpness,
        decision="rejected" if reasons else "accepted",
        reasons=tuple(reasons),
    )


def _laplacian_variance(bgr: NDArray[np.uint8], detection: FaceDetection) -> float:
    height, width = bgr.shape[:2]
    box = detection.bounding_box
    left = max(0, floor(box.x))
    top = max(0, floor(box.y))
    right = min(width, ceil(box.x + box.width))
    bottom = min(height, ceil(box.y + box.height))
    if right <= left or bottom <= top:
        return 0.0
    crop = bgr[top:bottom, left:right]
    grayscale = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grayscale, cv2.CV_64F).var())
