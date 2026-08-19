import json
import os
import subprocess
import sys
from pathlib import Path

from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

SELFIE_SEARCH_CHECK_TAG = "selfie_search"
BACKEND_DIR = Path(__file__).resolve().parents[2]


def load_isolated_selfie_settings(**environment_overrides: str) -> dict[str, object]:
    environment = os.environ.copy()
    for name in (
        "SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES",
        "SELFIE_SEARCH_MAX_PIXELS",
        "SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS",
        "SELFIE_SEARCH_EMBEDDING_MODEL",
        "SELFIE_SEARCH_EMBEDDING_DIMENSIONS",
        "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD",
        "ADAFACE_LOCAL_EXPERIMENT_ENABLED",
        "ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD",
        "DEBUG",
        "SELFIE_SEARCH_TEMPORARY_PREFIX",
        "SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS",
        "SELFIE_FEEDBACK_ENABLED",
        "SELFIE_FEEDBACK_S3_BUCKET",
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID",
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY",
        "SELFIE_FEEDBACK_S3_ENDPOINT_URL",
        "SELFIE_FEEDBACK_S3_REGION",
        "SELFIE_FEEDBACK_KMS_KEY_ID",
        "SELFIE_FEEDBACK_MAX_UPLOAD_BYTES",
        "SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS",
    ):
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
print(json.dumps({name: getattr(settings, name) for name in json.loads(__import__("sys").argv[1])}))
"""
    names = [
        "SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES",
        "SELFIE_SEARCH_MAX_PIXELS",
        "SELFIE_SEARCH_EMBEDDING_MODEL",
        "SELFIE_SEARCH_EMBEDDING_DIMENSIONS",
        "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD",
        "ADAFACE_LOCAL_EXPERIMENT_ENABLED",
        "SELFIE_SEARCH_TEMPORARY_PREFIX",
        "SELFIE_FEEDBACK_ENABLED",
        "SELFIE_FEEDBACK_S3_BUCKET",
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID",
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY",
        "SELFIE_FEEDBACK_KMS_KEY_ID",
        "SELFIE_FEEDBACK_MAX_UPLOAD_BYTES",
        "SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS",
    ]
    completed = subprocess.run(
        [sys.executable, "-c", script, json.dumps(names)],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return json.loads(completed.stdout)


class SelfieSearchSettingsTests(SimpleTestCase):
    def test_logging_routes_selfie_info_events_to_console(self) -> None:
        from django.conf import settings

        logger = settings.LOGGING["loggers"]["selfie_search"]
        handler = settings.LOGGING["handlers"]["selfie_console"]

        self.assertEqual(
            logger, {"handlers": ["selfie_console"], "level": "INFO", "propagate": False}
        )
        self.assertEqual(handler, {"class": "logging.StreamHandler", "level": "INFO"})

    def test_selfie_contract_values_are_always_parsed_and_validated(self) -> None:
        """A malformed normal selfie limit must prevent startup, not leave a hidden feature."""
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(SELFIE_SEARCH_MAX_UPLOAD_BYTES="not-a-number")

    def test_defaults_are_the_approved_bounded_contract(self) -> None:
        from django.conf import settings

        self.assertIs(settings.SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED, False)
        self.assertEqual(settings.SELFIE_SEARCH_MAX_UPLOAD_BYTES, 20 * 1024 * 1024)
        self.assertEqual(settings.SELFIE_SEARCH_MAX_PIXELS, 25_000_000)
        self.assertEqual(settings.SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS, 120)
        self.assertEqual(settings.SELFIE_SEARCH_EMBEDDING_MODEL, "sface")
        self.assertEqual(settings.SELFIE_SEARCH_EMBEDDING_DIMENSIONS, 128)
        self.assertEqual(settings.SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD, 0.363)
        self.assertEqual(settings.SELFIE_SEARCH_TEMPORARY_PREFIX, "selfie-search/")
        self.assertEqual(settings.SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS, 24)
        self.assertIs(settings.SELFIE_FEEDBACK_ENABLED, False)
        self.assertEqual(settings.SELFIE_FEEDBACK_S3_BUCKET, "")
        self.assertEqual(settings.SELFIE_FEEDBACK_S3_ACCESS_KEY_ID, "")
        self.assertEqual(settings.SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY, "")
        self.assertEqual(settings.SELFIE_FEEDBACK_KMS_KEY_ID, "")
        self.assertEqual(settings.SELFIE_FEEDBACK_MAX_UPLOAD_BYTES, 20 * 1024 * 1024)
        self.assertEqual(settings.SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS, 60)

    def test_adaface_requires_the_explicit_local_gate_and_non_sface_threshold(self) -> None:
        """A non-debug process or inherited SFace threshold must never start the experiment."""
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(ADAFACE_LOCAL_EXPERIMENT_ENABLED="True")
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(
                ADAFACE_LOCAL_EXPERIMENT_ENABLED="True",
                ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD="0.363",
            )
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(
                DEBUG="False",
                ADAFACE_LOCAL_EXPERIMENT_ENABLED="True",
                ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD="0.42",
            )

        values = load_isolated_selfie_settings(
            DEBUG="True",
            ADAFACE_LOCAL_EXPERIMENT_ENABLED="True",
            ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD="0.42",
        )

        self.assertEqual(values["SELFIE_SEARCH_EMBEDDING_MODEL"], "adaface-ir18-webface4m")
        self.assertEqual(values["SELFIE_SEARCH_EMBEDDING_DIMENSIONS"], 512)
        self.assertEqual(values["SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD"], 0.42)
        self.assertIs(values["ADAFACE_LOCAL_EXPERIMENT_ENABLED"], True)

    def test_disabled_feedback_uses_safe_defaults_without_parsing_dormant_overrides(self) -> None:
        values = load_isolated_selfie_settings(
            SELFIE_FEEDBACK_ENABLED="False",
            SELFIE_FEEDBACK_MAX_UPLOAD_BYTES="not-a-number",
            SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS="also-not-a-number",
        )

        self.assertEqual(
            values,
            {
                "SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED": False,
                "SELFIE_SEARCH_MAX_UPLOAD_BYTES": 20 * 1024 * 1024,
                "SELFIE_SEARCH_MAX_PIXELS": 25_000_000,
                "SELFIE_SEARCH_EMBEDDING_MODEL": "sface",
                "SELFIE_SEARCH_EMBEDDING_DIMENSIONS": 128,
                "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD": 0.363,
                "ADAFACE_LOCAL_EXPERIMENT_ENABLED": False,
                "SELFIE_SEARCH_TEMPORARY_PREFIX": "selfie-search/",
                "SELFIE_FEEDBACK_ENABLED": False,
                "SELFIE_FEEDBACK_S3_BUCKET": "",
                "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "",
                "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "",
                "SELFIE_FEEDBACK_KMS_KEY_ID": "",
                "SELFIE_FEEDBACK_MAX_UPLOAD_BYTES": 20 * 1024 * 1024,
                "SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS": 60,
            },
        )

    def test_enabled_feedback_requires_complete_safe_configuration(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(SELFIE_FEEDBACK_ENABLED="True")
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(
                SELFIE_FEEDBACK_ENABLED="True",
                SELFIE_FEEDBACK_S3_BUCKET="feedback-bucket",
                SELFIE_FEEDBACK_S3_ACCESS_KEY_ID="access-key",
                SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="secret-key",
                SELFIE_FEEDBACK_KMS_KEY_ID="kms-key",
                SELFIE_FEEDBACK_MAX_UPLOAD_BYTES="1",
            )
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(
                SELFIE_FEEDBACK_ENABLED="True",
                PRIVATE_MEDIA_S3_BUCKET="feedback-bucket",
                SELFIE_FEEDBACK_S3_BUCKET="feedback-bucket",
                SELFIE_FEEDBACK_S3_ACCESS_KEY_ID="access-key",
                SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="secret-key",
                SELFIE_FEEDBACK_KMS_KEY_ID="kms-key",
            )
        for unsafe_override in (
            {"SELFIE_FEEDBACK_S3_ENDPOINT_URL": "https://storage.attacker.example"},
            {"SELFIE_FEEDBACK_S3_REGION": "us-east-1"},
        ):
            with self.subTest(unsafe_override=unsafe_override):
                with self.assertRaises(subprocess.CalledProcessError):
                    load_isolated_selfie_settings(
                        SELFIE_FEEDBACK_ENABLED="True",
                        SELFIE_FEEDBACK_S3_BUCKET="feedback-bucket",
                        SELFIE_FEEDBACK_S3_ACCESS_KEY_ID="access-key",
                        SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="secret-key",
                        SELFIE_FEEDBACK_KMS_KEY_ID="kms-key",
                        **unsafe_override,
                    )

    @override_settings(SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=True)
    def test_cluster_expansion_is_independent_of_feedback_storage(self) -> None:
        errors = run_checks(tags=[SELFIE_SEARCH_CHECK_TAG])

        self.assertNotIn("selfie_search.E010", [error.id for error in errors])

    @override_settings(
        SELFIE_FEEDBACK_ENABLED=True,
        SELFIE_FEEDBACK_S3_BUCKET="private-selfies",
        PRIVATE_MEDIA_S3_BUCKET="private-selfies",
        SELFIE_FEEDBACK_S3_ACCESS_KEY_ID=" ",
        SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="secret-key",
        SELFIE_FEEDBACK_KMS_KEY_ID="kms-key",
        SELFIE_FEEDBACK_MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS=60,
    )
    def test_enabled_feedback_rejects_shared_bucket_and_blank_credentials(self) -> None:
        errors = run_checks(tags=[SELFIE_SEARCH_CHECK_TAG])

        self.assertEqual(
            {error.id for error in errors if error.id.startswith("selfie_search.E00")},
            {"selfie_search.E008", "selfie_search.E009"},
        )

    @override_settings(
        PHOTO_PROCESSING_ENABLED=True,
        PHOTO_PROCESSING_FACE_ENABLED=True,
        SELFIE_FEEDBACK_ENABLED=True,
        SELFIE_FEEDBACK_S3_BUCKET="feedback-bucket",
        PRIVATE_MEDIA_S3_BUCKET="private-media-bucket",
        SELFIE_FEEDBACK_S3_ACCESS_KEY_ID="access-key",
        SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="secret-key",
        SELFIE_FEEDBACK_S3_ENDPOINT_URL="https://storage.yandexcloud.net",
        SELFIE_FEEDBACK_S3_REGION="ru-central1",
        SELFIE_FEEDBACK_KMS_KEY_ID="kms-key",
        SELFIE_FEEDBACK_MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS=60,
    )
    def test_system_check_rejects_non_yandex_feedback_endpoint_or_region(self) -> None:
        for unsafe_override in (
            {"SELFIE_FEEDBACK_S3_ENDPOINT_URL": "https://storage.attacker.example"},
            {"SELFIE_FEEDBACK_S3_REGION": "us-east-1"},
        ):
            with self.subTest(unsafe_override=unsafe_override):
                with self.settings(**unsafe_override):
                    errors = run_checks(tags=[SELFIE_SEARCH_CHECK_TAG])

                self.assertEqual([error.id for error in errors], ["selfie_search.E008"])
