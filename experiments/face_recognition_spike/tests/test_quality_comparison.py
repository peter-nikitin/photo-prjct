from __future__ import annotations

from dataclasses import replace

import pytest
from face_spike.analysis import BoundingBox


def _quality(
    *,
    decision: str = "accepted",
    reasons: tuple[str, ...] = (),
    confidence: float = 0.9,
    minimum_side_px: float = 40.0,
    relative_area: float = 0.1,
    sharpness: float = 60.0,
):
    from photo_worker.face_quality import FaceQualityEvidence

    return FaceQualityEvidence(
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        confidence=confidence,
        minimum_side_px=minimum_side_px,
        relative_area=relative_area,
        sharpness=sharpness,
        decision=decision,
        reasons=reasons,
    )


def _face(
    face_id: str,
    filename: str,
    bbox: tuple[float, float, float, float],
    *,
    status: str = "accepted",
    quality=None,
    technical_failure: str | None = None,
):
    from face_spike.quality_comparison import QualityFace

    return QualityFace(
        face_id=face_id,
        filename=filename,
        bounding_box=BoundingBox(*bbox),
        crop_path=f"faces/{face_id}.png",
        status=status,
        quality=quality or _quality(),
        technical_failure=technical_failure,
    )


def _run(
    *photos,
    media_hash: str = "b" * 64,
    inventory_hash: str = "a" * 64,
    detector_threshold: float = 0.75,
):
    from face_spike.quality_comparison import QualityRun

    return QualityRun(
        run_sha256="c" * 64,
        inventory_sha256=inventory_hash,
        media_sha256=tuple(
            (photo.filename, media_hash if photo.filename == "photo.jpg" else "d" * 64)
            for photo in sorted(photos, key=lambda item: item.filename)
        ),
        generation_sha256="e" * 64,
        photos=tuple(photos),
        model_hashes=(("sface", "f" * 64), ("yunet", "1" * 64)),
        non_quality_configuration={"detection_threshold": detector_threshold},
        quality_configuration={
            "algorithm_version": "normalized-laplacian-v1",
            "crop_size": 112,
            "minimum_face_px": 32,
            "severe_blur_threshold": 25.0,
            "borderline_blur_threshold": 50.0,
            "minimum_relative_area": 0.05,
            "minimum_confidence": 0.82,
        },
    )


def _photo(filename: str, *faces, status: str = "ok", technical_failure: str | None = None):
    from face_spike.quality_comparison import QualityPhoto

    return QualityPhoto(filename, status, tuple(faces), technical_failure)


def _thresholds():
    from photo_worker.face_quality import FaceQualityThresholds

    return FaceQualityThresholds(
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        minimum_face_px=32,
        severe_blur_threshold=25.0,
        borderline_blur_threshold=50.0,
        minimum_relative_area=0.05,
        minimum_confidence=0.82,
    )


def test_comparison_requires_the_exact_inventory_and_media_hashes() -> None:
    from face_spike.quality_comparison import compare_quality_runs

    photo = _photo("photo.jpg", _face("old", "photo.jpg", (0, 0, 20, 20)))
    baseline = _run(photo)

    with pytest.raises(ValueError, match="inventory"):
        compare_quality_runs(
            baseline,
            _run(photo, inventory_hash="f" * 64),
            thresholds=_thresholds(),
        )
    with pytest.raises(ValueError, match="media"):
        compare_quality_runs(
            baseline,
            _run(photo, media_hash="f" * 64),
            thresholds=_thresholds(),
        )


def test_comparison_matches_boxes_deterministically_and_counts_terminal_outcomes() -> None:
    from face_spike.quality_comparison import compare_quality_runs

    baseline = _run(
        _photo(
            "photo.jpg",
            _face("baseline-a", "photo.jpg", (0, 0, 20, 20)),
            _face("baseline-b", "photo.jpg", (30, 0, 20, 20)),
            _face("baseline-old-only", "photo.jpg", (70, 0, 10, 10)),
        ),
        _photo("unresolved.jpg", status="image_decode_failed", technical_failure="decode"),
    )
    candidate = _run(
        _photo(
            "photo.jpg",
            _face(
                "candidate-rejected",
                "photo.jpg",
                (1, 0, 20, 20),
                status="quality_rejected",
                quality=_quality(
                    decision="quality_rejected", reasons=("severe_blur",), sharpness=5
                ),
            ),
            _face("candidate-retained", "photo.jpg", (31, 0, 20, 20)),
            _face("candidate-new-only", "photo.jpg", (90, 0, 8, 8)),
            _face(
                "candidate-technical",
                "photo.jpg",
                (110, 0, 8, 8),
                status="technical_failed",
                quality=None,
                technical_failure="model_inference_error",
            ),
        ),
        _photo("unresolved.jpg", status="detection_failed", technical_failure="detector"),
    )

    result = compare_quality_runs(baseline, candidate, thresholds=_thresholds())

    assert result.counts == {
        "baseline_detected": 3,
        "baseline_accepted": 3,
        "baseline_rejected": 0,
        "baseline_embedded": 3,
        "baseline_technical_failures": 0,
        "candidate_detected": 4,
        "matched": 2,
        "retained": 1,
        "newly_rejected": 1,
        "old_only": 1,
        "new_only": 2,
        "candidate_accepted": 2,
        "candidate_rejected": 1,
        "candidate_embedded": 2,
        "candidate_technical_failures": 1,
    }
    assert [(match.baseline_face_id, match.candidate_face_id) for match in result.matches] == [
        ("baseline-a", "candidate-rejected"),
        ("baseline-b", "candidate-retained"),
    ]
    assert [face.candidate_face_id for face in result.new_rejections] == ["candidate-rejected"]
    assert result.unresolved_photos == ("photo.jpg", "unresolved.jpg")
    assert [
        (item.cohort, item.filename, item.face_id, item.reason)
        for item in result.technical_failures
    ] == [
        ("baseline", "unresolved.jpg", None, "decode"),
        ("candidate", "photo.jpg", "candidate-technical", "model_inference_error"),
        ("candidate", "unresolved.jpg", None, "detector"),
    ]
    assert result.rejection_reason_counts == {
        "baseline": {},
        "candidate": {"severe_blur": 1},
    }
    assert result.technical_reason_counts == {
        "baseline": {"decode": 1},
        "candidate": {"detector": 1, "model_inference_error": 1},
    }


def test_threshold_band_sampling_is_deterministic_and_retained_only() -> None:
    from face_spike.quality_comparison import compare_quality_runs

    near = _quality(
        confidence=0.83,
        minimum_side_px=33,
        relative_area=0.051,
        sharpness=51,
    )
    baseline_face = _face("baseline", "photo.jpg", (0, 0, 33, 33), quality=near)
    candidate_face = _face("candidate", "photo.jpg", (0, 0, 33, 33), quality=near)
    baseline = _run(_photo("photo.jpg", baseline_face), _photo("unresolved.jpg"))
    candidate = _run(_photo("photo.jpg", candidate_face), _photo("unresolved.jpg"))

    result = compare_quality_runs(
        baseline,
        candidate,
        thresholds=_thresholds(),
        threshold_band_fraction=0.05,
        samples_per_metric=1,
    )

    assert {
        metric: tuple(sample.face_id for sample in samples)
        for metric, samples in result.threshold_samples.items()
    } == {
        "borderline_blur_threshold": ("candidate",),
        "minimum_confidence": ("candidate",),
        "minimum_face_px": ("candidate",),
        "minimum_relative_area": ("candidate",),
    }
    with pytest.raises(ValueError, match="threshold samples do not reconcile"):
        replace(result, threshold_samples={})
    rejected = replace(
        candidate_face,
        status="quality_rejected",
        quality=_quality(decision="quality_rejected", reasons=("severe_blur",), sharpness=5),
    )
    rejected_result = compare_quality_runs(
        baseline,
        _run(_photo("photo.jpg", rejected), _photo("unresolved.jpg")),
        thresholds=_thresholds(),
    )
    assert all(
        all(sample.face_id != "candidate" for sample in values)
        for values in rejected_result.threshold_samples.values()
    )


def test_comparison_rejects_non_quality_configuration_drift_and_unmatched_faces() -> None:
    from face_spike.quality_comparison import compare_quality_runs

    baseline = _run(_photo("photo.jpg", _face("baseline", "photo.jpg", (0, 0, 20, 20))))
    with pytest.raises(ValueError, match="non-quality"):
        compare_quality_runs(
            baseline,
            _run(
                _photo("photo.jpg", _face("candidate", "photo.jpg", (0, 0, 20, 20))),
                detector_threshold=0.5,
            ),
            thresholds=_thresholds(),
        )

    result = compare_quality_runs(
        baseline,
        _run(_photo("photo.jpg")),
        thresholds=_thresholds(),
    )
    assert result.counts["old_only"] == 1
    assert result.unresolved_photos == ("photo.jpg",)
