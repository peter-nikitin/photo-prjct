from __future__ import annotations

import numpy as np
import pytest


def _thresholds(**overrides: object):
    from photo_worker.face_quality import FaceQualityThresholds

    return FaceQualityThresholds(
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        minimum_face_px=20,
        severe_blur_threshold=10.0,
        borderline_blur_threshold=20.0,
        minimum_relative_area=0.1,
        minimum_confidence=0.8,
        **overrides,
    )


def _image() -> np.ndarray:
    pixels = np.zeros((100, 100, 3), dtype=np.uint8)
    checkerboard = np.indices((60, 60)).sum(axis=0) % 2 * 255
    pixels[20:80, 20:80] = checkerboard[..., None]
    return pixels


def test_rejects_a_face_smaller_than_the_physical_minimum() -> None:
    """A regression to accepting unusably small background faces must be caught."""
    from photo_worker.face_quality import evaluate_face_quality

    evidence = evaluate_face_quality(
        _image(),
        bbox=(20.0, 20.0, 19.0, 19.0),
        confidence=0.99,
        thresholds=_thresholds(),
    )

    assert evidence.decision == "quality_rejected"
    assert evidence.reasons == ("too_small",)


def test_rejects_severe_blur_even_for_a_large_confident_face() -> None:
    """Removing the severe-blur branch must reject no faces and fail this test."""
    from photo_worker.face_quality import evaluate_face_quality

    evidence = evaluate_face_quality(
        np.zeros((100, 100, 3), dtype=np.uint8),
        bbox=(20.0, 20.0, 60.0, 60.0),
        confidence=0.99,
        thresholds=_thresholds(),
    )

    assert evidence.decision == "quality_rejected"
    assert evidence.reasons == ("severe_blur",)


def test_rejects_borderline_blur_only_with_a_second_weak_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping corroboration would retain a deliberately weak borderline face."""
    import photo_worker.face_quality as face_quality

    monkeypatch.setattr(face_quality, "_normalized_crop_sharpness", lambda *_args, **_kwargs: 15.0)

    evidence = face_quality.evaluate_face_quality(
        _image(),
        bbox=(20.0, 20.0, 20.0, 20.0),
        confidence=0.99,
        thresholds=_thresholds(),
    )

    assert evidence.decision == "quality_rejected"
    assert evidence.reasons == ("borderline_blur", "small_relative_area")


@pytest.mark.parametrize(
    ("sharpness", "bbox", "confidence"),
    [
        (15.0, (20.0, 20.0, 60.0, 60.0), 0.99),
        (30.0, (20.0, 20.0, 20.0, 20.0), 0.99),
        (30.0, (20.0, 20.0, 60.0, 60.0), 0.79),
    ],
)
def test_retains_every_single_borderline_signal(
    monkeypatch: pytest.MonkeyPatch,
    sharpness: float,
    bbox: tuple[float, float, float, float],
    confidence: float,
) -> None:
    """Changing recall-first policy to reject one weak metric must be visible."""
    import photo_worker.face_quality as face_quality

    monkeypatch.setattr(
        face_quality, "_normalized_crop_sharpness", lambda *_args, **_kwargs: sharpness
    )

    evidence = face_quality.evaluate_face_quality(
        _image(), bbox=bbox, confidence=confidence, thresholds=_thresholds()
    )

    assert evidence.decision == "accepted"
    assert evidence.reasons == ()


def test_inclusive_threshold_boundaries_remain_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing strict below-threshold comparisons to inclusive comparisons is a recall loss."""
    import photo_worker.face_quality as face_quality

    monkeypatch.setattr(face_quality, "_normalized_crop_sharpness", lambda *_args, **_kwargs: 10.0)
    severe_boundary = face_quality.evaluate_face_quality(
        _image(),
        bbox=(20.0, 20.0, 60.0, 60.0),
        confidence=0.8,
        thresholds=_thresholds(),
    )
    monkeypatch.setattr(face_quality, "_normalized_crop_sharpness", lambda *_args, **_kwargs: 20.0)
    borderline_boundary = face_quality.evaluate_face_quality(
        _image(),
        bbox=(20.0, 20.0, 20.0, 20.0),
        confidence=0.8,
        thresholds=_thresholds(),
    )

    assert severe_boundary.decision == "accepted"
    assert borderline_boundary.decision == "accepted"


@pytest.mark.parametrize(
    ("bbox", "confidence"),
    [
        ((float("nan"), 20.0, 20.0, 20.0), 0.99),
        ((20.0, 20.0, float("inf"), 20.0), 0.99),
        ((20.0, 20.0, 0.0, 20.0), 0.99),
        ((90.0, 20.0, 20.0, 20.0), 0.99),
        ((20.0, 20.0, 20.0, 20.0), float("nan")),
    ],
)
def test_invalid_measurements_fail_as_technical_errors(
    bbox: tuple[float, float, float, float], confidence: float
) -> None:
    """Invalid geometry must not be silently converted into a quality rejection."""
    from photo_worker.face_quality import FaceQualityError, evaluate_face_quality

    with pytest.raises(FaceQualityError, match="invalid_face_quality"):
        evaluate_face_quality(_image(), bbox=bbox, confidence=confidence, thresholds=_thresholds())


def test_sharpness_uses_the_fixed_grayscale_crop_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing fixed normalization would make crop scores incomparable across face sizes."""
    import photo_worker.face_quality as face_quality

    calls: list[tuple[tuple[int, int], int]] = []
    real_resize = face_quality.cv2.resize

    def record_resize(source: np.ndarray, size: tuple[int, int], interpolation: int) -> np.ndarray:
        calls.append((size, interpolation))
        return real_resize(source, size, interpolation=interpolation)

    monkeypatch.setattr(face_quality.cv2, "resize", record_resize)

    evidence = face_quality.evaluate_face_quality(
        _image(),
        bbox=(20.0, 20.0, 60.0, 60.0),
        confidence=0.99,
        thresholds=_thresholds(),
    )

    assert evidence.crop_size == 112
    assert calls == [((112, 112), face_quality.cv2.INTER_AREA)]


@pytest.mark.parametrize(
    ("decision", "reasons"),
    [("accepted", ("severe_blur",)), ("quality_rejected", ())],
)
def test_evidence_rejects_a_decision_that_contradicts_its_reasons(
    decision: str, reasons: tuple[str, ...]
) -> None:
    """A terminal quality record must not claim acceptance and a rejection reason at once."""
    from photo_worker.face_quality import FaceQualityError, FaceQualityEvidence

    with pytest.raises(FaceQualityError, match="invalid_face_quality"):
        FaceQualityEvidence(
            algorithm_version="normalized-laplacian-v1",
            crop_size=112,
            confidence=0.99,
            minimum_side_px=30.0,
            relative_area=0.2,
            sharpness=30.0,
            decision=decision,
            reasons=reasons,
        )
