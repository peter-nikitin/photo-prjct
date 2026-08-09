"""Frozen, fully serialized quality decisions for the local preview experiment."""

from __future__ import annotations

from dataclasses import dataclass

from photo_worker.face_quality import FaceQualityEvidence, FaceQualityThresholds


@dataclass(frozen=True)
class DecisionConfiguration:
    algorithm_version: str = "normalized-laplacian-v1"
    crop_size: int = 112
    minimum_face_px: int = 32
    severe_blur_threshold: float = 25
    borderline_blur_threshold: float = 50
    minimum_relative_area: float = 0.0009
    minimum_confidence: float = 0.82
    detector_thresholds: tuple[float, ...] = (0.75, 0.70, 0.65)

    def thresholds(self) -> FaceQualityThresholds:
        return FaceQualityThresholds(
            self.algorithm_version,
            self.crop_size,
            self.minimum_face_px,
            self.severe_blur_threshold,
            self.borderline_blur_threshold,
            self.minimum_relative_area,
            self.minimum_confidence,
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "algorithm_version": self.algorithm_version,
            "crop_size": self.crop_size,
            "minimum_face_px": self.minimum_face_px,
            "severe_blur_threshold": self.severe_blur_threshold,
            "borderline_blur_threshold": self.borderline_blur_threshold,
            "minimum_relative_area": self.minimum_relative_area,
            "minimum_confidence": self.minimum_confidence,
            "detector_thresholds": list(self.detector_thresholds),
        }


@dataclass(frozen=True)
class QualityProfile:
    name: str
    candidate_minimum_side_px: float | None = None
    candidate_background_blur_threshold: float | None = None

    def as_payload(self, configuration: DecisionConfiguration) -> dict[str, object]:
        return {
            "name": self.name,
            "decision_configuration": configuration.as_payload(),
            "candidate_minimum_side_px": self.candidate_minimum_side_px,
            "candidate_background_blur_threshold": self.candidate_background_blur_threshold,
        }


@dataclass(frozen=True)
class QualityDecision:
    decision: str
    reasons: tuple[str, ...]


DECISION_CONFIGURATION = DecisionConfiguration()
QUALITY_PROFILES = (
    QualityProfile("current-v3"),
    QualityProfile("small-floor-40", candidate_minimum_side_px=40),
    QualityProfile("background-blur-75", candidate_background_blur_threshold=75),
    QualityProfile(
        "combined-40-75",
        candidate_minimum_side_px=40,
        candidate_background_blur_threshold=75,
    ),
)


def profile_payloads() -> list[dict[str, object]]:
    return [profile.as_payload(DECISION_CONFIGURATION) for profile in QUALITY_PROFILES]


def decide_quality(profile: QualityProfile, evidence: FaceQualityEvidence) -> QualityDecision:
    """Apply additions to one measured production decision, never a global confidence floor."""
    if profile not in QUALITY_PROFILES:
        raise ValueError("unknown quality profile")
    reasons = list(evidence.reasons)
    if (
        profile.candidate_minimum_side_px is not None
        and evidence.minimum_side_px < profile.candidate_minimum_side_px
    ):
        reasons.append("candidate_too_small")
    if (
        profile.candidate_background_blur_threshold is not None
        and evidence.relative_area < DECISION_CONFIGURATION.minimum_relative_area
        and evidence.sharpness < profile.candidate_background_blur_threshold
    ):
        reasons.append("candidate_background_blur")
    unique = tuple(dict.fromkeys(reasons))
    return QualityDecision("quality_rejected" if unique else "accepted", unique)
