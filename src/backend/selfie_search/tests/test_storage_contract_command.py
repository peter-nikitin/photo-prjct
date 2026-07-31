from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
from typing import Any
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from ingestion.tests.fakes import client_error


class RecordingS3Client:
    def __init__(
        self, lifecycle: Mapping[str, object], *, versioning: Mapping[str, object] | None = None
    ) -> None:
        self.lifecycle = dict(lifecycle)
        self.versioning = dict(versioning or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("get_bucket_versioning", kwargs))
        return self.versioning

    def get_bucket_lifecycle_configuration(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("get_bucket_lifecycle_configuration", kwargs))
        return self.lifecycle

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("put_object", kwargs))
        return {}

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("head_object", kwargs))
        return {"ContentLength": len(kwargs["Key"]), "ContentType": "image/jpeg"}

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self.calls.append(("generate_presigned_url", kwargs))
        return "https://storage.example.test/grant?signature=secret"

    def delete_object(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("delete_object", kwargs))
        return {}


class PutFailureS3Client(RecordingS3Client):
    def put_object(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("put_object", kwargs))
        raise client_error(500, "PutObject")


LIFECYCLE = {
    "Rules": [
        {
            "ID": "selfie-search-expire",
            "Status": "Enabled",
            "Filter": {"Prefix": "selfie-search/"},
            "Expiration": {"Days": 1},
        }
    ]
}


@override_settings(
    PRIVATE_MEDIA_S3_BUCKET="private-selfies",
    SELFIE_SEARCH_TEMPORARY_PREFIX="selfie-search/",
    SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS=24,
)
class VerifySelfieSearchStorageCommandTests(SimpleTestCase):
    def test_uses_one_generated_private_key_and_emits_sanitized_success_markers(self) -> None:
        client = RecordingS3Client(LIFECYCLE)
        output = StringIO()

        with patch(
            "selfie_search.management.commands.verify_selfie_search_storage.boto3.client",
            return_value=client,
        ):
            call_command("verify_selfie_search_storage", "--confirm-real-storage", stdout=output)

        operations = [operation for operation, _ in client.calls]
        self.assertEqual(
            operations,
            [
                "get_bucket_versioning",
                "get_bucket_lifecycle_configuration",
                "put_object",
                "head_object",
                "generate_presigned_url",
                "delete_object",
            ],
        )
        scratch_key = client.calls[2][1]["Key"]
        self.assertRegex(scratch_key, r"^selfie-search/[0-9a-f]{32}$")
        self.assertEqual(client.calls[2][1]["ACL"], "private")
        self.assertEqual(client.calls[3][1]["Key"], scratch_key)
        self.assertEqual(client.calls[4][1]["Params"]["Key"], scratch_key)
        self.assertEqual(client.calls[5][1]["Key"], scratch_key)
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "selfie-search-storage-preflight-versioning-unversioned-ok",
                "selfie-search-storage-preflight-lifecycle-ok",
                "selfie-search-storage-preflight-put-ok",
                "selfie-search-storage-preflight-head-ok",
                "selfie-search-storage-preflight-grant-ok",
                "selfie-search-storage-preflight-delete-ok",
            ],
        )
        self.assertNotIn(scratch_key, output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_refuses_to_write_without_an_explicit_real_storage_confirmation(self) -> None:
        client = RecordingS3Client(LIFECYCLE)

        with patch(
            "selfie_search.management.commands.verify_selfie_search_storage.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "--confirm-real-storage"):
                call_command("verify_selfie_search_storage")

        self.assertEqual(client.calls, [])

    def test_fails_closed_when_the_exact_prefix_is_not_bounded_to_24_hours(self) -> None:
        client = RecordingS3Client(
            {
                "Rules": [
                    {
                        "Status": "Enabled",
                        "Filter": {"Prefix": "selfie-search/"},
                        "Expiration": {"Days": 2},
                    }
                ]
            }
        )

        with patch(
            "selfie_search.management.commands.verify_selfie_search_storage.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "lifecycle"):
                call_command("verify_selfie_search_storage", "--confirm-real-storage")

        self.assertEqual(
            [operation for operation, _ in client.calls],
            ["get_bucket_versioning", "get_bucket_lifecycle_configuration"],
        )

    def test_refuses_enabled_or_suspended_versioning_before_lifecycle_or_scratch_object(
        self,
    ) -> None:
        for status in ("Enabled", "Suspended", None):
            with self.subTest(status=status):
                client = RecordingS3Client(LIFECYCLE, versioning={"Status": status})

                with patch(
                    "selfie_search.management.commands.verify_selfie_search_storage.boto3.client",
                    return_value=client,
                ):
                    with self.assertRaisesMessage(CommandError, "versioning"):
                        call_command("verify_selfie_search_storage", "--confirm-real-storage")

                self.assertEqual(
                    [operation for operation, _ in client.calls], ["get_bucket_versioning"]
                )

    def test_refuses_versioning_read_error_before_lifecycle_or_scratch_object(self) -> None:
        class VersioningFailureClient(RecordingS3Client):
            def get_bucket_versioning(self, **kwargs: Any) -> dict[str, object]:
                self.calls.append(("get_bucket_versioning", kwargs))
                raise client_error(500, "GetBucketVersioning")

        client = VersioningFailureClient(LIFECYCLE)

        with patch(
            "selfie_search.management.commands.verify_selfie_search_storage.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "versioning"):
                call_command("verify_selfie_search_storage", "--confirm-real-storage")

        self.assertEqual([operation for operation, _ in client.calls], ["get_bucket_versioning"])

    def test_attempts_generated_key_cleanup_even_when_private_put_fails(self) -> None:
        client = PutFailureS3Client(LIFECYCLE)

        with patch(
            "selfie_search.management.commands.verify_selfie_search_storage.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "storage preflight failed"):
                call_command("verify_selfie_search_storage", "--confirm-real-storage")

        self.assertEqual(
            [operation for operation, _ in client.calls],
            [
                "get_bucket_versioning",
                "get_bucket_lifecycle_configuration",
                "put_object",
                "delete_object",
            ],
        )
