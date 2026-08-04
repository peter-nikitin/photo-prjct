from __future__ import annotations

from email.message import Message
from io import StringIO
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

LIFECYCLE = {
    "Rules": [
        {
            "ID": "selfie-feedback-expire-after-30d",
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": 30},
        }
    ]
}


class RecordingS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.versioning: dict[str, object] = {}
        self.object_lock: dict[str, object] = {}
        self.encryption: dict[str, object] = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": "kms-feedback-key",
                        }
                    }
                ]
            }
        }
        self.acl: dict[str, object] = {"Grants": []}
        self.lifecycle = LIFECYCLE

    def _record(self, operation: str, kwargs: dict[str, Any]) -> dict[str, object]:
        self.calls.append((operation, kwargs))
        return {}

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, object]:
        self._record("get_bucket_versioning", kwargs)
        return self.versioning

    def get_object_lock_configuration(self, **kwargs: Any) -> dict[str, object]:
        self._record("get_object_lock_configuration", kwargs)
        return self.object_lock

    def get_bucket_encryption(self, **kwargs: Any) -> dict[str, object]:
        self._record("get_bucket_encryption", kwargs)
        return self.encryption

    def get_bucket_acl(self, **kwargs: Any) -> dict[str, object]:
        self._record("get_bucket_acl", kwargs)
        return self.acl

    def get_bucket_lifecycle_configuration(self, **kwargs: Any) -> dict[str, Any]:
        self._record("get_bucket_lifecycle_configuration", kwargs)
        return self.lifecycle

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        return self._record("put_object", kwargs)

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        self._record("head_object", kwargs)
        return {"ContentLength": 33, "ContentType": "image/jpeg"}

    def get_object(self, **kwargs: Any) -> dict[str, object]:
        self._record("get_object", kwargs)

        class Body:
            def read(self, amount: int) -> bytes:  # noqa: ARG002
                return b"x"

            def close(self) -> None:
                return None

        return {"Body": Body()}

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self._record("generate_presigned_url", kwargs)
        return "https://storage.example.test/grant?signature=secret"

    def delete_object(self, **kwargs: Any) -> dict[str, object]:
        return self._record("delete_object", kwargs)


class AnonymousForbiddenOpener:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: int) -> object:  # noqa: ARG002
        self.requests.append(request)
        url = request.full_url  # type: ignore[attr-defined]
        raise HTTPError(url, 403, "forbidden", Message(), None)


class AnonymousUnsafeOpener(AnonymousForbiddenOpener):
    def open(self, request: object, *, timeout: int) -> object:  # noqa: ARG002
        self.requests.append(request)

        class Response:
            def getcode(self) -> int:
                return 302

            def close(self) -> None:
                return None

        return Response()


@override_settings(
    SELFIE_FEEDBACK_S3_BUCKET="feedback-private",
    SELFIE_FEEDBACK_KMS_KEY_ID="kms-feedback-key",
    SELFIE_FEEDBACK_MAX_UPLOAD_BYTES=20 * 1024 * 1024,
    SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS=60,
)
class VerifyFeedbackStorageCommandTests(SimpleTestCase):
    def test_checks_private_bucket_contract_and_runs_one_sanitized_scratch_cycle(self) -> None:
        client = RecordingS3Client()
        anonymous_opener = AnonymousForbiddenOpener()
        output = StringIO()

        with (
            patch(
                "selfie_search.management.commands.verify_selfie_feedback_storage.boto3.client",
                return_value=client,
            ),
            patch(
                "selfie_search.management.commands.verify_selfie_feedback_storage.build_opener",
                return_value=anonymous_opener,
                create=True,
            ),
        ):
            call_command("verify_selfie_feedback_storage", "--confirm-real-storage", stdout=output)

        operations = [operation for operation, _ in client.calls]
        self.assertEqual(
            operations,
            [
                "get_bucket_versioning",
                "get_object_lock_configuration",
                "get_bucket_encryption",
                "get_bucket_acl",
                "get_bucket_lifecycle_configuration",
                "put_object",
                "head_object",
                "get_object",
                "generate_presigned_url",
                "delete_object",
            ],
        )
        self.assertTrue(
            all(
                kwargs.get("Bucket", kwargs.get("Params", {}).get("Bucket")) == "feedback-private"
                for _, kwargs in client.calls
            )
        )
        scratch_key = client.calls[5][1]["Key"]
        self.assertRegex(scratch_key, r"^[0-9a-f]{32}$")
        self.assertEqual(client.calls[5][1]["ACL"], "private")
        self.assertEqual(client.calls[5][1]["ServerSideEncryption"], "aws:kms")
        self.assertEqual(client.calls[5][1]["SSEKMSKeyId"], "kms-feedback-key")
        self.assertEqual(client.calls[7][1]["Range"], "bytes=0-0")
        self.assertEqual(client.calls[8][1]["ExpiresIn"], 60)
        self.assertEqual(client.calls[9][1]["Key"], scratch_key)
        self.assertEqual(len(anonymous_opener.requests), 3)
        object_get, object_head, bucket_list = anonymous_opener.requests
        self.assertEqual(object_get.get_method(), "GET")  # type: ignore[attr-defined]
        self.assertEqual(object_head.get_method(), "HEAD")  # type: ignore[attr-defined]
        self.assertEqual(bucket_list.get_method(), "GET")  # type: ignore[attr-defined]
        self.assertEqual(
            object_get.full_url,  # type: ignore[attr-defined]
            f"https://feedback-private.storage.yandexcloud.net/{scratch_key}",
        )
        self.assertEqual(
            bucket_list.full_url,  # type: ignore[attr-defined]
            "https://feedback-private.storage.yandexcloud.net/?list-type=2",
        )
        self.assertTrue(
            all(
                request.get_header("Authorization") is None  # type: ignore[attr-defined]
                for request in anonymous_opener.requests
            )
        )
        self.assertNotIn(scratch_key, output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_fails_before_scratch_object_for_wrong_kms(self) -> None:
        client = RecordingS3Client()
        client.encryption = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": "other-key",
                        }
                    }
                ]
            }
        }
        with patch(
            "selfie_search.management.commands.verify_selfie_feedback_storage.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "encryption"):
                call_command("verify_selfie_feedback_storage", "--confirm-real-storage")
        self.assertNotIn("put_object", [operation for operation, _ in client.calls])

    def test_rejects_anonymous_redirect_and_still_cleans_the_generated_scratch_object(self) -> None:
        client = RecordingS3Client()
        anonymous_opener = AnonymousUnsafeOpener()
        with (
            patch(
                "selfie_search.management.commands.verify_selfie_feedback_storage.boto3.client",
                return_value=client,
            ),
            patch(
                "selfie_search.management.commands.verify_selfie_feedback_storage.build_opener",
                return_value=anonymous_opener,
                create=True,
            ),
        ):
            with self.assertRaisesMessage(CommandError, "anonymous"):
                call_command("verify_selfie_feedback_storage", "--confirm-real-storage")

        operations = [operation for operation, _ in client.calls]
        self.assertEqual(operations[-1], "delete_object")
        self.assertEqual(len(anonymous_opener.requests), 1)

    def test_refuses_a_real_write_without_confirmation(self) -> None:
        client = RecordingS3Client()
        with patch(
            "selfie_search.management.commands.verify_selfie_feedback_storage.boto3.client",
            return_value=client,
        ):
            with self.assertRaisesMessage(CommandError, "confirm-real-storage"):
                call_command("verify_selfie_feedback_storage")
        self.assertEqual(client.calls, [])
