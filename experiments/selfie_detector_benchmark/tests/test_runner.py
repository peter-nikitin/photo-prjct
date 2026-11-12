from __future__ import annotations

import pytest
from detector_benchmark.offline import validate_experiment_revision
from detector_benchmark.runner import Detection, classify_detections, validate_detector_cohort


def _detection(confidence: float) -> Detection:
    return Detection(x=1, y=2, width=50, height=60, confidence=confidence, landmarks=())


def test_quality_variant_counts_only_accepted_detections() -> None:
    """A raw banner face after the quality gate would turn a usable selfie into multiple faces."""
    outcome = classify_detections(
        (_detection(0.95), _detection(0.76)),
        accepted=(True, False),
        quality_enabled=True,
    )

    assert outcome.raw_detection_count == 2
    assert outcome.accepted_detection_count == 1
    assert outcome.outcome == "single_face"


def test_two_quality_accepted_faces_remain_a_multiple_face_rejection() -> None:
    """Dropping an accepted second face would violate the multiple-face guardrail."""
    outcome = classify_detections(
        (_detection(0.95), _detection(0.94)),
        accepted=(True, True),
        quality_enabled=True,
    )

    assert outcome.accepted_detection_count == 2
    assert outcome.outcome == "multiple_faces"


def test_detector_cohort_requires_the_frozen_control_distribution() -> None:
    """Dropping a successful or multiple-face control would weaken the claimed guardrails."""
    records = (
        *({"cohort": "no_face"} for _ in range(17)),
        *({"cohort": "successful_control"} for _ in range(16)),
        *({"cohort": "multiple_faces"} for _ in range(3)),
    )

    validate_detector_cohort(records)

    with pytest.raises(ValueError, match="distribution"):
        validate_detector_cohort(records[:-1] + ({"cohort": "no_face"},))


def test_experiment_revision_must_be_a_nonempty_immutable_sha() -> None:
    """An empty Docker environment variable must not produce an unidentifiable run."""
    validate_experiment_revision("a" * 40)
    with pytest.raises(ValueError, match="nonempty"):
        validate_experiment_revision("")
