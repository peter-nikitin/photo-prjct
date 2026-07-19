from __future__ import annotations

import urllib.error
from datetime import timedelta
from email.message import Message
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from ingestion.management.commands import verify_private_upload_storage as command_module
from ingestion.storage import ObjectIdentity, UploadGrant


class ContractStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        return UploadGrant(
            url="https://storage.invalid/bucket",
            fields={
                "key": incoming_key,
                "Content-Type": "image/jpeg",
                "policy": "signed-policy",
            },
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def inspect(self, *, key: str) -> ObjectIdentity:
        return self._identity()

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes:
        return command_module._JPEG[start : end + 1]

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity:
        return self._identity()

    def delete(self, *, key: str) -> None:
        self.deleted.append(key)

    @staticmethod
    def _identity() -> ObjectIdentity:
        return ObjectIdentity(
            etag_wire='"contract-etag"',
            etag_value="contract-etag",
            size=len(command_module._JPEG),
            content_type="image/jpeg",
        )


def rejected_request() -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://storage.invalid", 403, "Forbidden", Message(), None)


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    PHOTO_UPLOAD_MAX_FILE_BYTES=52_428_800,
    PRIVATE_MEDIA_ALLOWED_ORIGINS=["https://findme-photo.ru"],
    PRIVATE_MEDIA_S3_ENDPOINT_URL="https://storage.yandexcloud.net",
    PRIVATE_MEDIA_S3_BUCKET="hires-staging",
)
class VerifyPrivateStorageCommandTests(SimpleTestCase):
    def test_requires_explicit_real_storage_confirmation(self) -> None:
        with self.assertRaisesMessage(CommandError, "--confirm-real-storage"):
            call_command("verify_private_upload_storage", origin="https://findme-photo.ru")

    def test_rejects_an_origin_outside_the_configured_allowlist(self) -> None:
        with self.assertRaisesMessage(CommandError, "exactly match"):
            call_command(
                "verify_private_upload_storage",
                confirm_real_storage=True,
                origin="https://attacker.invalid",
            )

    def test_exercises_policy_privacy_cors_and_always_cleans_objects(self) -> None:
        storage = ContractStorage()
        output = StringIO()
        request_results = [
            (403, {}),
            (
                200,
                {
                    "access-control-allow-origin": "https://findme-photo.ru",
                    "access-control-allow-methods": "POST",
                    "access-control-allow-headers": "content-type",
                },
            ),
        ]
        with (
            patch.object(command_module, "PrivateUploadStorage", return_value=storage),
            patch.object(
                command_module,
                "_post_form",
                side_effect=[rejected_request(), rejected_request(), rejected_request(), None],
            ) as post_form,
            patch.object(command_module, "_request", side_effect=request_results) as request,
        ):
            call_command(
                "verify_private_upload_storage",
                confirm_real_storage=True,
                origin="https://findme-photo.ru",
                stdout=output,
            )

        self.assertEqual(post_form.call_count, 4)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(storage.deleted), 2)
        self.assertIn("verified", output.getvalue())
        self.assertNotIn("signed-policy", output.getvalue())
        self.assertNotIn("incoming/", output.getvalue())


@override_settings(PHOTO_UPLOAD_ENABLED=False)
class VerifyPrivateStorageDisabledTests(SimpleTestCase):
    def test_refuses_to_write_when_the_feature_is_disabled(self) -> None:
        with self.assertRaisesMessage(CommandError, "must be True"):
            call_command(
                "verify_private_upload_storage",
                confirm_real_storage=True,
                origin="https://findme-photo.ru",
            )
