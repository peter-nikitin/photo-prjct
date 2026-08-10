from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


_BUNDLE_SHA256 = "a" * 64


def _rejection(face_id: str, reasons: tuple[str, ...]) -> Any:
    from face_spike.analysis import BoundingBox
    from face_spike.quality_comparison import NewRejection

    minimum_side_px = 10.0 if "too_small" in reasons else 40.0
    sharpness = 10.0 if "severe_blur" in reasons else 60.0
    confidence = 0.7 if "low_confidence" in reasons else 0.9
    relative_area = 0.01 if "small_relative_area" in reasons else 0.1
    return NewRejection(
        filename=f"{face_id}.jpg",
        baseline_face_id=f"baseline-{face_id}",
        candidate_face_id=face_id,
        crop_path=f"faces/{face_id}.png",
        bounding_box=BoundingBox(0, 0, 20, 20),
        reasons=reasons,
        confidence=confidence,
        minimum_side_px=minimum_side_px,
        relative_area=relative_area,
        sharpness=sharpness,
    )


def _comparison(*rejections: Any) -> SimpleNamespace:
    return SimpleNamespace(new_rejections=tuple(rejections))


def test_build_selects_exact_unique_population_representing_every_canonical_stratum() -> None:
    from face_spike.quality_sample import build_quality_sample

    rejections = tuple(
        _rejection(f"face-{index:04d}", ("severe_blur",)) for index in range(1598)
    ) + (
        _rejection("small", ("too_small",)),
        _rejection("borderline", ("borderline_blur", "low_confidence")),
    )

    sample = build_quality_sample(_comparison(*rejections), _BUNDLE_SHA256, sample_size=1506)

    assert sample.population_count == 1600
    assert len(sample.rejections) == 1506
    assert len({item.face_id for item in sample.rejections}) == 1506
    assert [(item.reasons, item.population_count, item.sample_count) for item in sample.strata] == [
        (("borderline_blur", "low_confidence"), 1, 1),
        (("severe_blur",), 1598, 1504),
        (("too_small",), 1, 1),
    ]


def test_build_allocates_rare_stratum_minimum_then_capacity_aware_largest_remainder() -> None:
    from face_spike.quality_sample import build_quality_sample

    rejections = (
        _rejection("rare", ("too_small",)),
        *(_rejection(f"blur-{index}", ("severe_blur",)) for index in range(2)),
        *(_rejection(f"both-{index}", ("too_small", "severe_blur")) for index in range(7)),
    )

    sample = build_quality_sample(_comparison(*rejections), _BUNDLE_SHA256, sample_size=7)

    assert [(item.reasons, item.population_count, item.sample_count) for item in sample.strata] == [
        (("severe_blur",), 2, 2),
        (("too_small",), 1, 1),
        (("too_small", "severe_blur"), 7, 4),
    ]


def test_build_uses_bundle_hash_and_face_id_for_selection_not_input_order() -> None:
    from face_spike.quality_sample import build_quality_sample

    rejections = tuple(_rejection(f"face-{index}", ("severe_blur",)) for index in range(8))
    sample = build_quality_sample(_comparison(*rejections), _BUNDLE_SHA256, sample_size=3)
    reverse_sample = build_quality_sample(
        _comparison(*reversed(rejections)), _BUNDLE_SHA256, sample_size=3
    )
    expected = tuple(
        face_id
        for _digest, face_id in sorted(
            (
                hashlib.sha256(
                    f"{_BUNDLE_SHA256}\0{rejection.candidate_face_id}".encode()
                ).hexdigest(),
                rejection.candidate_face_id,
            )
            for rejection in rejections
        )[:3]
    )

    assert tuple(item.face_id for item in sample.rejections) == expected
    assert tuple(item.face_id for item in reverse_sample.rejections) == expected


def test_build_reconciles_population_and_sample_metadata_and_validates_inputs() -> None:
    from face_spike.quality_sample import build_quality_sample

    rejections = (
        _rejection("first", ("severe_blur",)),
        _rejection("second", ("severe_blur",)),
        _rejection("third", ("too_small",)),
    )
    sample = build_quality_sample(_comparison(*rejections), _BUNDLE_SHA256, sample_size=3)

    assert sum(item.population_count for item in sample.strata) == sample.population_count
    assert sum(item.sample_count for item in sample.strata) == len(sample.rejections)
    assert all(item.inclusion_weight == 1.0 for item in sample.rejections)

    with pytest.raises(ValueError, match="bundle"):
        build_quality_sample(_comparison(*rejections), "not-a-digest", sample_size=3)
    with pytest.raises(ValueError, match="sample size"):
        build_quality_sample(_comparison(*rejections), _BUNDLE_SHA256, sample_size=0)
    with pytest.raises(ValueError, match="duplicate"):
        build_quality_sample(
            _comparison(rejections[0], rejections[0]), _BUNDLE_SHA256, sample_size=2
        )
    with pytest.raises(ValueError, match="empty"):
        build_quality_sample(_comparison(), _BUNDLE_SHA256, sample_size=1)
    with pytest.raises(ValueError, match="every stratum"):
        build_quality_sample(_comparison(*rejections), _BUNDLE_SHA256, sample_size=1)


def test_analysis_reports_weighted_labels_and_deterministic_review_lists() -> None:
    from face_spike.quality_sample import (
        QualitySample,
        QualitySampleLabel,
        QualitySampleStratum,
        SampledRejection,
        analyze_quality_sample,
    )

    clear = _rejection("clear", ("too_small",))
    blurred = _rejection("blurred", ("severe_blur",))
    small = _rejection("small", ("severe_blur",))
    uncertain = _rejection("uncertain", ("severe_blur",))
    sample = QualitySample(
        source_bundle_sha256=_BUNDLE_SHA256,
        population_count=10,
        strata=(
            QualitySampleStratum(("severe_blur",), 6, 3),
            QualitySampleStratum(("too_small",), 4, 1),
        ),
        rejections=(
            SampledRejection(blurred, 6, 3),
            SampledRejection(clear, 4, 1),
            SampledRejection(small, 6, 3),
            SampledRejection(uncertain, 6, 3),
        ),
    )

    analysis = analyze_quality_sample(
        sample,
        (
            QualitySampleLabel("clear", "clear"),
            QualitySampleLabel("blurred", "blurred"),
            QualitySampleLabel("small", "unusably_small"),
            QualitySampleLabel("uncertain", "uncertain"),
        ),
    )

    assert analysis.raw_counts == {
        "clear": 1,
        "blurred": 1,
        "unusably_small": 1,
        "uncertain": 1,
    }
    assert analysis.weighted_counts == {
        "clear": 4.0,
        "blurred": 2.0,
        "unusably_small": 2.0,
        "uncertain": 2.0,
    }
    assert analysis.weighted_proportions == {
        "clear": 0.4,
        "blurred": 0.2,
        "unusably_small": 0.2,
        "uncertain": 0.2,
    }
    assert analysis.kish_effective_sample_size == pytest.approx(25 / 7)
    assert analysis.clear_wilson_interval[0] == pytest.approx(0.0953712)
    assert analysis.clear_wilson_interval[1] == pytest.approx(0.8082715)
    assert tuple(item.face_id for item in analysis.clear_rejections) == ("clear",)
    assert tuple(item.face_id for item in analysis.uncertain_rejections) == ("uncertain",)


def test_analysis_requires_exact_complete_valid_labels_and_clamps_wilson_interval() -> None:
    from face_spike.quality_sample import (
        QualitySample,
        QualitySampleLabel,
        QualitySampleStratum,
        SampledRejection,
        analyze_quality_sample,
    )

    rejection = _rejection("only", ("severe_blur",))
    sample = QualitySample(
        _BUNDLE_SHA256,
        1,
        (QualitySampleStratum(("severe_blur",), 1, 1),),
        (SampledRejection(rejection, 1, 1),),
    )

    with pytest.raises(ValueError, match="complete"):
        analyze_quality_sample(sample, ())
    with pytest.raises(ValueError, match="duplicate"):
        analyze_quality_sample(
            sample,
            (QualitySampleLabel("only", "clear"), QualitySampleLabel("only", "clear")),
        )
    with pytest.raises(ValueError, match="label"):
        analyze_quality_sample(sample, (QualitySampleLabel("only", "wrong"),))

    analysis = analyze_quality_sample(sample, (QualitySampleLabel("only", "clear"),))
    assert analysis.raw_counts == {
        "clear": 1,
        "blurred": 0,
        "unusably_small": 0,
        "uncertain": 0,
    }
    assert 0.0 <= analysis.clear_wilson_interval[0] <= analysis.clear_wilson_interval[1] <= 1.0
