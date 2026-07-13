import pytest
from django.conf import settings
from django.core.checks import run_checks
from django.test import override_settings

PHOTO_UPLOAD_CHECK_TAG = "photo_upload"

VALID_PRIVATE_SETTINGS = {
    "PHOTO_UPLOAD_ENABLED": True,
    "PRIVATE_MEDIA_S3_BUCKET": "private-originals",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "test-access-key",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "test-secret-key",
    "PRIVATE_MEDIA_S3_ENDPOINT_URL": "https://storage.example.test",
    "PRIVATE_MEDIA_S3_REGION": "test-region-1",
    "PRIVATE_MEDIA_ALLOWED_ORIGINS": ["https://photos.example.test"],
}


def photo_upload_errors(**overrides):
    configured_settings = VALID_PRIVATE_SETTINGS | overrides
    with override_settings(**configured_settings):
        return run_checks(tags=[PHOTO_UPLOAD_CHECK_TAG])


def test_photo_upload_defaults_are_approved_security_caps() -> None:
    assert settings.PHOTO_UPLOAD_ENABLED is False
    assert settings.PHOTO_UPLOAD_MAX_FILES == 10_000
    assert settings.PHOTO_UPLOAD_MAX_FILE_BYTES == 50 * 1024 * 1024
    assert settings.PHOTO_UPLOAD_REGISTRATION_CHUNK == 100
    assert settings.PHOTO_UPLOAD_CONCURRENCY == 4
    assert settings.PHOTO_UPLOAD_GRANT_TTL_SECONDS == 600
    assert settings.PHOTO_UPLOAD_STALE_AFTER_SECONDS == 86_400


@pytest.mark.parametrize(
    ("setting_name", "missing_value"),
    [
        ("PRIVATE_MEDIA_S3_BUCKET", ""),
        ("PRIVATE_MEDIA_S3_ACCESS_KEY_ID", ""),
        ("PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY", ""),
        ("PRIVATE_MEDIA_S3_ENDPOINT_URL", ""),
        ("PRIVATE_MEDIA_S3_REGION", ""),
        ("PRIVATE_MEDIA_ALLOWED_ORIGINS", []),
    ],
)
def test_enabled_uploads_require_non_empty_private_storage_settings(
    setting_name: str,
    missing_value: str | list[str],
) -> None:
    errors = photo_upload_errors(**{setting_name: missing_value})

    assert len(errors) == 1
    assert setting_name in errors[0].msg


@pytest.mark.parametrize(
    ("setting_name", "invalid_value"),
    [
        ("PHOTO_UPLOAD_MAX_FILES", 0),
        ("PHOTO_UPLOAD_MAX_FILES", 10_001),
        ("PHOTO_UPLOAD_MAX_FILE_BYTES", 0),
        ("PHOTO_UPLOAD_MAX_FILE_BYTES", 52_428_801),
        ("PHOTO_UPLOAD_REGISTRATION_CHUNK", 0),
        ("PHOTO_UPLOAD_REGISTRATION_CHUNK", 101),
        ("PHOTO_UPLOAD_CONCURRENCY", 0),
        ("PHOTO_UPLOAD_CONCURRENCY", 5),
        ("PHOTO_UPLOAD_GRANT_TTL_SECONDS", 59),
        ("PHOTO_UPLOAD_GRANT_TTL_SECONDS", 601),
        ("PHOTO_UPLOAD_STALE_AFTER_SECONDS", 86_399),
    ],
)
def test_enabled_uploads_reject_values_outside_approved_bounds(
    setting_name: str,
    invalid_value: int,
) -> None:
    errors = photo_upload_errors(**{setting_name: invalid_value})

    assert len(errors) == 1
    assert setting_name in errors[0].msg


@pytest.mark.parametrize(
    "limits",
    [
        {
            "PHOTO_UPLOAD_MAX_FILES": 1,
            "PHOTO_UPLOAD_MAX_FILE_BYTES": 1,
            "PHOTO_UPLOAD_REGISTRATION_CHUNK": 1,
            "PHOTO_UPLOAD_CONCURRENCY": 1,
            "PHOTO_UPLOAD_GRANT_TTL_SECONDS": 60,
            "PHOTO_UPLOAD_STALE_AFTER_SECONDS": 86_400,
        },
        {
            "PHOTO_UPLOAD_MAX_FILES": 10_000,
            "PHOTO_UPLOAD_MAX_FILE_BYTES": 52_428_800,
            "PHOTO_UPLOAD_REGISTRATION_CHUNK": 100,
            "PHOTO_UPLOAD_CONCURRENCY": 4,
            "PHOTO_UPLOAD_GRANT_TTL_SECONDS": 600,
            "PHOTO_UPLOAD_STALE_AFTER_SECONDS": 172_800,
        },
    ],
)
def test_enabled_uploads_accept_values_within_approved_bounds(limits: dict[str, int]) -> None:
    assert photo_upload_errors(**limits) == []


def test_disabled_uploads_do_not_require_private_storage_configuration() -> None:
    with override_settings(
        PHOTO_UPLOAD_ENABLED=False,
        PRIVATE_MEDIA_S3_BUCKET="",
        PRIVATE_MEDIA_S3_ACCESS_KEY_ID="",
        PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY="",
        PRIVATE_MEDIA_S3_ENDPOINT_URL="",
        PRIVATE_MEDIA_S3_REGION="",
        PRIVATE_MEDIA_ALLOWED_ORIGINS=[],
    ):
        assert run_checks(tags=[PHOTO_UPLOAD_CHECK_TAG]) == []


def test_security_caps_are_validated_while_uploads_are_disabled() -> None:
    with override_settings(PHOTO_UPLOAD_ENABLED=False, PHOTO_UPLOAD_CONCURRENCY=5):
        errors = run_checks(tags=[PHOTO_UPLOAD_CHECK_TAG])

    assert len(errors) == 1
    assert "PHOTO_UPLOAD_CONCURRENCY" in errors[0].msg
