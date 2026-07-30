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
        "SELFIE_SEARCH_ENABLED",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES",
        "SELFIE_SEARCH_MAX_PIXELS",
        "SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS",
        "SELFIE_SEARCH_EMBEDDING_MODEL",
        "SELFIE_SEARCH_EMBEDDING_DIMENSIONS",
        "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD",
        "SELFIE_SEARCH_TEMPORARY_PREFIX",
        "SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS",
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
        "SELFIE_SEARCH_ENABLED",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES",
        "SELFIE_SEARCH_MAX_PIXELS",
        "SELFIE_SEARCH_EMBEDDING_MODEL",
        "SELFIE_SEARCH_TEMPORARY_PREFIX",
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
    def test_disabled_defaults_are_the_approved_bounded_contract(self) -> None:
        from django.conf import settings

        self.assertIs(settings.SELFIE_SEARCH_ENABLED, False)
        self.assertEqual(settings.SELFIE_SEARCH_MAX_UPLOAD_BYTES, 20 * 1024 * 1024)
        self.assertEqual(settings.SELFIE_SEARCH_MAX_PIXELS, 25_000_000)
        self.assertEqual(settings.SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS, 120)
        self.assertEqual(settings.SELFIE_SEARCH_EMBEDDING_MODEL, "sface")
        self.assertEqual(settings.SELFIE_SEARCH_EMBEDDING_DIMENSIONS, 128)
        self.assertEqual(settings.SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD, 0.363)
        self.assertEqual(settings.SELFIE_SEARCH_TEMPORARY_PREFIX, "selfie-search/")
        self.assertEqual(settings.SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS, 24)

    @override_settings(SELFIE_SEARCH_ENABLED="True")
    def test_feature_flag_must_remain_a_boolean(self) -> None:
        errors = run_checks(tags=[SELFIE_SEARCH_CHECK_TAG])

        self.assertIn("selfie_search.E001", [error.id for error in errors])

    def test_disabled_feature_uses_safe_defaults_without_parsing_dormant_overrides(self) -> None:
        values = load_isolated_selfie_settings(
            SELFIE_SEARCH_ENABLED="False",
            SELFIE_SEARCH_MAX_UPLOAD_BYTES="not-a-number",
            SELFIE_SEARCH_MAX_PIXELS="also-not-a-number",
            SELFIE_SEARCH_EMBEDDING_MODEL="different-model",
            SELFIE_SEARCH_TEMPORARY_PREFIX="originals/",
        )

        self.assertEqual(
            values,
            {
                "SELFIE_SEARCH_ENABLED": False,
                "SELFIE_SEARCH_MAX_UPLOAD_BYTES": 20 * 1024 * 1024,
                "SELFIE_SEARCH_MAX_PIXELS": 25_000_000,
                "SELFIE_SEARCH_EMBEDDING_MODEL": "sface",
                "SELFIE_SEARCH_TEMPORARY_PREFIX": "selfie-search/",
            },
        )

    def test_enabled_feature_fails_closed_on_malformed_required_override(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            load_isolated_selfie_settings(
                SELFIE_SEARCH_ENABLED="True",
                SELFIE_SEARCH_MAX_UPLOAD_BYTES="not-a-number",
            )
