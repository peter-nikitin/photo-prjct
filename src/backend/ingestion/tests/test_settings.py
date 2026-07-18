import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.checks import run_checks
from django.test import override_settings

PHOTO_UPLOAD_CHECK_TAG = "photo_upload"
BACKEND_DIR = Path(__file__).resolve().parents[2]

VALID_PRIVATE_SETTINGS = {
    "PHOTO_UPLOAD_ENABLED": True,
    "PRIVATE_MEDIA_S3_BUCKET": "private-originals",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "test-access-key",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "test-secret-key",
    "PRIVATE_MEDIA_S3_ENDPOINT_URL": "https://storage.example.test",
    "PRIVATE_MEDIA_S3_REGION": "test-region-1",
    "PRIVATE_MEDIA_ALLOWED_ORIGINS": ["https://photos.example.test"],
}

INGESTION_SETTING_NAMES = (
    "PRIVATE_MEDIA_ALLOWED_ORIGINS",
    "PHOTO_UPLOAD_ENABLED",
    "PHOTO_UPLOAD_MAX_FILES",
    "PHOTO_UPLOAD_MAX_FILE_BYTES",
    "PHOTO_UPLOAD_REGISTRATION_CHUNK",
    "PHOTO_UPLOAD_CONCURRENCY",
    "PHOTO_UPLOAD_GRANT_TTL_SECONDS",
    "PHOTO_UPLOAD_STALE_AFTER_SECONDS",
)

INGESTION_ENVIRONMENT_NAMES = (
    "PRIVATE_MEDIA_S3_BUCKET",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY",
    "PRIVATE_MEDIA_ALLOWED_ORIGINS",
    "PHOTO_UPLOAD_ENABLED",
    "PHOTO_UPLOAD_MAX_FILES",
    "PHOTO_UPLOAD_MAX_FILE_BYTES",
    "PHOTO_UPLOAD_REGISTRATION_CHUNK",
    "PHOTO_UPLOAD_CONCURRENCY",
    "PHOTO_UPLOAD_GRANT_TTL_SECONDS",
    "PHOTO_UPLOAD_STALE_AFTER_SECONDS",
)


def photo_upload_errors(**overrides):
    configured_settings = VALID_PRIVATE_SETTINGS | overrides
    with override_settings(**configured_settings):
        return run_checks(tags=[PHOTO_UPLOAD_CHECK_TAG])


def load_isolated_ingestion_settings(**environment_overrides):
    environment = os.environ.copy()
    for name in INGESTION_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    environment.update(
        {
            "DB_NAME": "test",
            "DB_USER": "test",
            "DB_PASSWORD": "test",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "MEDIA_STORAGE_BACKEND": "filesystem",
            "PYTHONPATH": str(BACKEND_DIR),
            "SECRET_KEY": "test-secret-key",
        }
    )
    environment.update(environment_overrides)
    script = """
import json
from unittest.mock import patch

with patch("environ.Env.read_env"):
    from config import settings

names = json.loads(__import__("sys").argv[1])
print(json.dumps({name: getattr(settings, name) for name in names}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, json.dumps(INGESTION_SETTING_NAMES)],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return json.loads(completed.stdout)


def test_photo_upload_defaults_are_approved_security_caps() -> None:
    isolated_settings = load_isolated_ingestion_settings()

    assert isolated_settings["PHOTO_UPLOAD_ENABLED"] is False
    assert isolated_settings["PHOTO_UPLOAD_MAX_FILES"] == 10_000
    assert isolated_settings["PHOTO_UPLOAD_MAX_FILE_BYTES"] == 50 * 1024 * 1024
    assert isolated_settings["PHOTO_UPLOAD_REGISTRATION_CHUNK"] == 100
    assert isolated_settings["PHOTO_UPLOAD_CONCURRENCY"] == 4
    assert isolated_settings["PHOTO_UPLOAD_GRANT_TTL_SECONDS"] == 600
    assert isolated_settings["PHOTO_UPLOAD_STALE_AFTER_SECONDS"] == 86_400


def test_private_media_origins_are_trimmed_when_parsed_from_environment() -> None:
    isolated_settings = load_isolated_ingestion_settings(
        PRIVATE_MEDIA_ALLOWED_ORIGINS="https://one.example.test, https://two.example.test"
    )

    assert isolated_settings["PRIVATE_MEDIA_ALLOWED_ORIGINS"] == [
        "https://one.example.test",
        "https://two.example.test",
    ]


def test_enabled_uploads_reject_blank_origin_members() -> None:
    isolated_settings = load_isolated_ingestion_settings(
        PRIVATE_MEDIA_ALLOWED_ORIGINS="https://one.example.test, "
    )

    errors = photo_upload_errors(
        PRIVATE_MEDIA_ALLOWED_ORIGINS=isolated_settings["PRIVATE_MEDIA_ALLOWED_ORIGINS"]
    )

    assert [error.id for error in errors] == ["ingestion.E006"]


@pytest.mark.parametrize(
    ("setting_name", "missing_value", "expected_id"),
    [
        ("PRIVATE_MEDIA_S3_BUCKET", "", "ingestion.E001"),
        ("PRIVATE_MEDIA_S3_ACCESS_KEY_ID", "", "ingestion.E002"),
        ("PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY", "", "ingestion.E003"),
        ("PRIVATE_MEDIA_S3_ENDPOINT_URL", "", "ingestion.E004"),
        ("PRIVATE_MEDIA_S3_REGION", "", "ingestion.E005"),
        ("PRIVATE_MEDIA_ALLOWED_ORIGINS", [], "ingestion.E006"),
    ],
)
def test_enabled_uploads_require_non_empty_private_storage_settings(
    setting_name: str,
    missing_value: str | list[str],
    expected_id: str,
) -> None:
    errors = photo_upload_errors(**{setting_name: missing_value})

    assert [error.id for error in errors] == [expected_id]
    assert setting_name in errors[0].msg


@pytest.mark.parametrize(
    ("setting_name", "invalid_value", "expected_id"),
    [
        ("PHOTO_UPLOAD_MAX_FILES", 0, "ingestion.E101"),
        ("PHOTO_UPLOAD_MAX_FILES", 10_001, "ingestion.E101"),
        ("PHOTO_UPLOAD_MAX_FILE_BYTES", 0, "ingestion.E102"),
        ("PHOTO_UPLOAD_MAX_FILE_BYTES", 52_428_801, "ingestion.E102"),
        ("PHOTO_UPLOAD_REGISTRATION_CHUNK", 0, "ingestion.E103"),
        ("PHOTO_UPLOAD_REGISTRATION_CHUNK", 101, "ingestion.E103"),
        ("PHOTO_UPLOAD_CONCURRENCY", 0, "ingestion.E104"),
        ("PHOTO_UPLOAD_CONCURRENCY", 5, "ingestion.E104"),
        ("PHOTO_UPLOAD_GRANT_TTL_SECONDS", 59, "ingestion.E105"),
        ("PHOTO_UPLOAD_GRANT_TTL_SECONDS", 601, "ingestion.E105"),
        ("PHOTO_UPLOAD_STALE_AFTER_SECONDS", 86_399, "ingestion.E106"),
    ],
)
def test_enabled_uploads_reject_values_outside_approved_bounds(
    setting_name: str,
    invalid_value: int,
    expected_id: str,
) -> None:
    errors = photo_upload_errors(**{setting_name: invalid_value})

    assert [error.id for error in errors] == [expected_id]
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

    assert [error.id for error in errors] == ["ingestion.E104"]
    assert "PHOTO_UPLOAD_CONCURRENCY" in errors[0].msg
