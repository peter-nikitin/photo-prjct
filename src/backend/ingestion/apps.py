from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register

PHOTO_UPLOAD_CHECK_TAG = "photo_upload"

_REQUIRED_PRIVATE_SETTINGS = (
    "PRIVATE_MEDIA_S3_BUCKET",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY",
    "PRIVATE_MEDIA_S3_ENDPOINT_URL",
    "PRIVATE_MEDIA_S3_REGION",
    "PRIVATE_MEDIA_ALLOWED_ORIGINS",
)

_BOUNDED_UPLOAD_SETTINGS = (
    ("PHOTO_UPLOAD_MAX_FILES", 1, 10_000),
    ("PHOTO_UPLOAD_MAX_FILE_BYTES", 1, 52_428_800),
    ("PHOTO_UPLOAD_REGISTRATION_CHUNK", 1, 100),
    ("PHOTO_UPLOAD_CONCURRENCY", 1, 4),
    ("PHOTO_UPLOAD_GRANT_TTL_SECONDS", 60, 600),
)


class IngestionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ingestion"
    verbose_name = "Photo ingestion"


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple)):
        return not any(str(item).strip() for item in value)
    return not str(value).strip()


@register(PHOTO_UPLOAD_CHECK_TAG)
def check_photo_upload_settings(
    app_configs: Iterable[AppConfig] | None,
    **kwargs: Any,
) -> list[Error]:
    del app_configs, kwargs
    errors = []

    if settings.PHOTO_UPLOAD_ENABLED:
        for index, setting_name in enumerate(_REQUIRED_PRIVATE_SETTINGS, start=1):
            if _is_empty(getattr(settings, setting_name)):
                errors.append(
                    Error(
                        f"{setting_name} must be non-empty when PHOTO_UPLOAD_ENABLED is True.",
                        id=f"ingestion.E{index:03d}",
                    )
                )

    for index, (setting_name, minimum, maximum) in enumerate(
        _BOUNDED_UPLOAD_SETTINGS,
        start=101,
    ):
        value = getattr(settings, setting_name)
        if not minimum <= value <= maximum:
            errors.append(
                Error(
                    f"{setting_name} must be between {minimum} and {maximum}.",
                    id=f"ingestion.E{index:03d}",
                )
            )

    if settings.PHOTO_UPLOAD_STALE_AFTER_SECONDS < 86_400:
        errors.append(
            Error(
                "PHOTO_UPLOAD_STALE_AFTER_SECONDS must be at least 86400.",
                id="ingestion.E106",
            )
        )

    return errors
