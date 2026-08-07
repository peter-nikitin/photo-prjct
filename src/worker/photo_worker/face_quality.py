"""Deterministic recall-first quality evidence for gallery-face detections."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2

_CROP_SIZE = 112
_ALGORITHM_VERSION = "normalized-laplacian-v1"
_MAX_LAPLACIAN_VARIANCE = 1_040_400.0
_MAX_FACE_SIDE_PX = 100_000_000


class FaceQualityError(ValueError):
    """A face measurement could not produce valid quality evidence."""

    def __init__(self, code: str = "invalid_face_quality") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FaceQualityThresholds:
    algorithm_version: str
    crop_size: int
    minimum_face_px: int
    severe_blur_threshold: float
    borderline_blur_threshold: float
    minimum_relative_area: float
    minimum_confidence: float

    def __post_init__(self) -> None:
        if not (
            self.algorithm_version == _ALGORITHM_VERSION
            and self.crop_size == _CROP_SIZE
            and _positive_int(self.minimum_face_px)
            and self.minimum_face_px <= _MAX_FACE_SIDE_PX
            and _bounded_nonnegative(self.severe_blur_threshold, maximum=_MAX_LAPLACIAN_VARIANCE)
            and _bounded_nonnegative(
                self.borderline_blur_threshold, maximum=_MAX_LAPLACIAN_VARIANCE
            )
            and self.severe_blur_threshold < self.borderline_blur_threshold
            and _bounded_probability(self.minimum_relative_area)
            and _bounded_probability(self.minimum_confidence)
        ):
            raise FaceQualityError()


@dataclass(frozen=True)
class FaceQualityEvidence:
    algorithm_version: str
    crop_size: int
    confidence: float
    minimum_side_px: float
    relative_area: float
    sharpness: float
    decision: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        valid_reasons = {
            ("too_small",),
            ("severe_blur",),
            ("too_small", "severe_blur"),
            ("borderline_blur", "small_relative_area"),
            ("borderline_blur", "low_confidence"),
            ("borderline_blur", "small_relative_area", "low_confidence"),
        }
        valid = (
            self.algorithm_version == _ALGORITHM_VERSION
            and self.crop_size == _CROP_SIZE
            and _bounded_probability(self.confidence)
            and _bounded_nonnegative(self.minimum_side_px, maximum=_MAX_FACE_SIDE_PX)
            and self.minimum_side_px > 0.0
            and _bounded_probability(self.relative_area)
            and _bounded_nonnegative(self.sharpness, maximum=_MAX_LAPLACIAN_VARIANCE)
            and self.decision in {"accepted", "quality_rejected"}
            and (
                (self.decision == "accepted" and not self.reasons)
                or (self.decision == "quality_rejected" and self.reasons in valid_reasons)
            )
        )
        if not valid:
            raise FaceQualityError()

    def as_payload(self) -> dict[str, object]:
        return {
            "algorithm_version": self.algorithm_version,
            "crop_size": self.crop_size,
            "confidence": self.confidence,
            "minimum_side_px": self.minimum_side_px,
            "relative_area": self.relative_area,
            "sharpness": self.sharpness,
            "decision": self.decision,
            "reasons": list(self.reasons),
        }


def evaluate_face_quality(
    image: Any,
    *,
    bbox: tuple[float, float, float, float],
    confidence: float,
    thresholds: FaceQualityThresholds,
) -> FaceQualityEvidence:
    """Measure one selected detection and apply the frozen recall-first decision table."""
    height, width = _image_dimensions(image)
    x, y, box_width, box_height = _valid_bbox(bbox, width=width, height=height)
    if not _bounded_probability(confidence):
        raise FaceQualityError()

    minimum_side = min(box_width, box_height)
    relative_area = box_width * box_height / (width * height)
    sharpness = _normalized_crop_sharpness(
        image,
        x=x,
        y=y,
        width=box_width,
        height=box_height,
        crop_size=thresholds.crop_size,
    )
    if not (
        math.isfinite(minimum_side)
        and 0.0 < minimum_side <= _MAX_FACE_SIDE_PX
        and _bounded_probability(relative_area)
        and _bounded_nonnegative(sharpness, maximum=_MAX_LAPLACIAN_VARIANCE)
    ):
        raise FaceQualityError()

    reasons: list[str] = []
    if minimum_side < thresholds.minimum_face_px:
        reasons.append("too_small")
    if sharpness < thresholds.severe_blur_threshold:
        reasons.append("severe_blur")
    if not reasons and sharpness < thresholds.borderline_blur_threshold:
        supporting_reasons: list[str] = []
        if relative_area < thresholds.minimum_relative_area:
            supporting_reasons.append("small_relative_area")
        if confidence < thresholds.minimum_confidence:
            supporting_reasons.append("low_confidence")
        if supporting_reasons:
            reasons.extend(("borderline_blur", *supporting_reasons))

    return FaceQualityEvidence(
        algorithm_version=thresholds.algorithm_version,
        crop_size=thresholds.crop_size,
        confidence=float(confidence),
        minimum_side_px=minimum_side,
        relative_area=relative_area,
        sharpness=sharpness,
        decision="quality_rejected" if reasons else "accepted",
        reasons=tuple(reasons),
    )


def _image_dimensions(image: Any) -> tuple[int, int]:
    try:
        height, width, channels = image.shape
    except (AttributeError, TypeError, ValueError) as error:
        raise FaceQualityError() from error
    if (
        not _positive_int(height)
        or not _positive_int(width)
        or channels != 3
        or height * width > _MAX_FACE_SIDE_PX
    ):
        raise FaceQualityError()
    return height, width


def _valid_bbox(
    bbox: tuple[float, float, float, float], *, width: int, height: int
) -> tuple[float, float, float, float]:
    if not isinstance(bbox, tuple) or len(bbox) != 4:
        raise FaceQualityError()
    try:
        x, y, box_width, box_height = (float(value) for value in bbox)
    except (TypeError, ValueError) as error:
        raise FaceQualityError() from error
    if not (
        all(math.isfinite(value) for value in (x, y, box_width, box_height))
        and x >= 0.0
        and y >= 0.0
        and box_width > 0.0
        and box_height > 0.0
        and x + box_width <= width
        and y + box_height <= height
    ):
        raise FaceQualityError()
    return x, y, box_width, box_height


def _normalized_crop_sharpness(
    image: Any, *, x: float, y: float, width: float, height: float, crop_size: int
) -> float:
    left, top = math.floor(x), math.floor(y)
    right, bottom = math.ceil(x + width), math.ceil(y + height)
    crop = image[top:bottom, left:right]
    if getattr(crop, "size", 0) == 0:
        raise FaceQualityError()
    try:
        grayscale = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        normalized = cv2.resize(grayscale, (crop_size, crop_size), interpolation=cv2.INTER_AREA)
        sharpness = float(cv2.Laplacian(normalized, cv2.CV_64F).var())
    except Exception as error:
        raise FaceQualityError() from error
    if not math.isfinite(sharpness):
        raise FaceQualityError()
    return sharpness


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _bounded_probability(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _bounded_nonnegative(value: object, *, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= maximum
    )
