import math

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register

SELFIE_SEARCH_CHECK_TAG = "selfie_search"


@register(SELFIE_SEARCH_CHECK_TAG)
def check_selfie_search_settings(**kwargs):  # noqa: ARG001
    """Fail closed on configuration that could widen temporary-data retention."""
    errors = []
    for name in ("SELFIE_SEARCH_ENABLED", "PHOTO_PROCESSING_FACE_ENABLED"):
        if not isinstance(getattr(settings, name), bool):
            errors.append(Error(f"{name} must be a boolean.", id="selfie_search.E001"))

    if settings.SELFIE_SEARCH_ENABLED is not True:
        return errors

    expected_values = {
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES": 20 * 1024 * 1024,
        "SELFIE_SEARCH_MAX_PIXELS": 25_000_000,
        "SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS": 120,
        "SELFIE_SEARCH_EMBEDDING_DIMENSIONS": 128,
        "SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS": 24,
    }
    for name, expected in expected_values.items():
        if getattr(settings, name) != expected:
            errors.append(
                Error(
                    f"{name} must be {expected!r} for the approved selfie-search contract.",
                    id="selfie_search.E002",
                )
            )
    if settings.SELFIE_SEARCH_EMBEDDING_MODEL != "sface":
        errors.append(
            Error(
                "SELFIE_SEARCH_EMBEDDING_MODEL must be 'sface'.",
                id="selfie_search.E003",
            )
        )
    threshold = settings.SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold != 0.363
    ):
        errors.append(
            Error(
                "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD must be the approved finite value 0.363.",
                id="selfie_search.E004",
            )
        )
    if settings.SELFIE_SEARCH_TEMPORARY_PREFIX != "selfie-search/":
        errors.append(
            Error(
                "SELFIE_SEARCH_TEMPORARY_PREFIX must be 'selfie-search/'.",
                id="selfie_search.E005",
            )
        )
    if not settings.PHOTO_PROCESSING_ENABLED or not settings.PHOTO_PROCESSING_FACE_ENABLED:
        errors.append(
            Error(
                "SELFIE_SEARCH_ENABLED requires enabled photo processing and face embeddings.",
                id="selfie_search.E006",
            )
        )
    return errors


class SelfieSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "selfie_search"
