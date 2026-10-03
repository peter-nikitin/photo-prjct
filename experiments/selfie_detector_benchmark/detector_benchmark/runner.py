from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Detection:
    x: float
    y: float
    width: float
    height: float
    confidence: float
    landmarks: tuple[float, ...]


@dataclass(frozen=True)
class DetectionOutcome:
    outcome: str
    raw_detection_count: int
    accepted_detection_count: int | None


VARIANTS = ("baseline-original", "normalized-1600", "normalized-1600-quality")
DETECTOR_COHORTS = frozenset({"no_face", "successful_control", "multiple_faces"})
_EXPECTED_COHORTS = {"no_face": 17, "successful_control": 16, "multiple_faces": 3}


def validate_detector_cohort(records: Sequence[Mapping[str, object]]) -> None:
    """Reject a snapshot whose detector controls do not match the frozen study population."""
    counts = Counter(record.get("cohort") for record in records)
    if counts != _EXPECTED_COHORTS:
        raise ValueError("detector cohort distribution is invalid")


def classify_detections(
    detections: Sequence[Detection], *, accepted: Sequence[bool] | None, quality_enabled: bool
) -> DetectionOutcome:
    """Apply the frozen cardinality rule, after quality only for the third variant."""
    raw_count = len(detections)
    if quality_enabled:
        if accepted is None or len(accepted) != raw_count:
            raise ValueError("quality decisions must match detections")
        count = sum(accepted)
        return DetectionOutcome(_cardinality(count), raw_count, count)
    if accepted is not None:
        raise ValueError("raw variants do not accept quality decisions")
    return DetectionOutcome(_cardinality(raw_count), raw_count, None)


def detection_payload(detection: Detection) -> dict[str, object]:
    return asdict(detection)


def _cardinality(count: int) -> str:
    if count == 0:
        return "no_face"
    if count == 1:
        return "single_face"
    return "multiple_faces"
