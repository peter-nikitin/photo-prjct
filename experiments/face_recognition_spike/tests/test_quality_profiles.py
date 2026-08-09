# ruff: noqa: E501

from photo_worker.face_quality import FaceQualityEvidence


def _evidence(
    *, confidence: float = 0.2, side: float = 50, area: float = 0.01, sharpness: float = 100
) -> FaceQualityEvidence:
    return FaceQualityEvidence(
        "normalized-laplacian-v1", 112, confidence, side, area, sharpness, "accepted", ()
    )


def test_frozen_profile_matrix_and_no_global_confidence_floor() -> None:
    from face_spike.quality_profiles import QUALITY_PROFILES, decide_quality

    assert tuple(profile.name for profile in QUALITY_PROFILES) == (
        "current-v3",
        "small-floor-40",
        "background-blur-75",
        "combined-40-75",
    )
    assert decide_quality(QUALITY_PROFILES[0], _evidence()).decision == "accepted"
    assert decide_quality(QUALITY_PROFILES[1], _evidence(side=35)).reasons == (
        "candidate_too_small",
    )
    assert decide_quality(QUALITY_PROFILES[2], _evidence(area=0.0001, sharpness=60)).reasons == (
        "candidate_background_blur",
    )
