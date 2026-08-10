"""Production face-quality interface used by the private local experiment."""

from __future__ import annotations

from photo_worker.face_quality import (
    FaceQualityError,
    FaceQualityEvidence,
    FaceQualityThresholds,
    evaluate_face_quality,
)

FaceQuality = FaceQualityEvidence

__all__ = [
    "FaceQuality",
    "FaceQualityError",
    "FaceQualityEvidence",
    "FaceQualityThresholds",
    "default_face_quality_thresholds",
    "evaluate_face_quality",
]


def default_face_quality_thresholds(*, minimum_face_px: int = 1) -> FaceQualityThresholds:
    """Return the permissive production-shape baseline used by legacy experiment commands."""
    return FaceQualityThresholds(
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        minimum_face_px=minimum_face_px,
        severe_blur_threshold=0.0,
        borderline_blur_threshold=1.0,
        minimum_relative_area=0.0,
        minimum_confidence=0.0,
    )
