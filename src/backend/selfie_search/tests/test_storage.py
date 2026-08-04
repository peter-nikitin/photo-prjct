import re
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from ingestion.storage import ObjectMissing, StorageUnavailable
from ingestion.tests.fakes import client_error
from selfie_search.storage import FeedbackSelfieStorage, TemporarySelfieStorage


class PutCapableFakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: dict[str, Exception] = {}
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.presigned_get_url = "https://download.example.test/private?signature=secret"

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        self._raise("put_object")
        self.objects[kwargs["Key"]] = (kwargs["Body"], kwargs["ContentType"])
        return {}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_object", kwargs))
        self._raise("head_object")
        try:
            content, content_type = self.objects[kwargs["Key"]]
        except KeyError:
            raise client_error(404, "NoSuchKey") from None
        return {"ContentLength": len(content), "ContentType": content_type}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_object", kwargs))
        self._raise("delete_object")
        return {}

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self.calls.append(("generate_presigned_url", kwargs))
        self._raise("generate_presigned_url")
        return self.presigned_get_url

    def _raise(self, operation: str) -> None:
        failure = self.failures.pop(operation, None)
        if failure is not None:
            raise failure


@override_settings(
    PRIVATE_MEDIA_S3_BUCKET="private-selfies",
    SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS=90,
    SELFIE_SEARCH_MAX_UPLOAD_BYTES=20 * 1024 * 1024,
)
class TemporarySelfieStorageTests(SimpleTestCase):
    """The production breaks caught here are granting worker access outside one private selfie."""

    key = "selfie-search/0123456789abcdef0123456789abcdef"

    def test_puts_only_a_bounded_private_jpeg_at_an_owned_key(self) -> None:
        client = PutCapableFakeS3Client()

        stored = TemporarySelfieStorage(client=client).put(
            key=self.key, content=b"jpeg", content_type="image/jpeg"
        )

        self.assertEqual(stored.key, self.key)
        self.assertEqual(stored.size, 4)
        self.assertEqual(
            client.calls,
            [
                (
                    "put_object",
                    {
                        "Bucket": "private-selfies",
                        "Key": self.key,
                        "Body": b"jpeg",
                        "ContentType": "image/jpeg",
                        "ContentLength": 4,
                        "ACL": "private",
                    },
                )
            ],
        )

    def test_rejects_nonowned_key_content_type_and_size(self) -> None:
        storage = TemporarySelfieStorage(client=PutCapableFakeS3Client())

        with self.assertRaises(ValueError):
            storage.put(key="originals/abc", content=b"x", content_type="image/jpeg")
        with self.assertRaises(ValueError):
            storage.put(key=self.key, content=b"x", content_type="image/gif")
        with self.assertRaises(ValueError):
            storage.put(
                key=self.key,
                content=b"x" * (20 * 1024 * 1024 + 1),
                content_type="image/png",
            )

    def test_inspects_only_a_valid_exact_temporary_object(self) -> None:
        client = PutCapableFakeS3Client()
        client.objects[self.key] = (b"jpeg", "image/jpeg")

        stored = TemporarySelfieStorage(client=client).inspect(key=self.key)

        self.assertEqual(
            (stored.key, stored.size, stored.content_type), (self.key, 4, "image/jpeg")
        )
        self.assertEqual(
            client.calls, [("head_object", {"Bucket": "private-selfies", "Key": self.key})]
        )

    def test_inspect_sanitizes_missing_or_invalid_objects(self) -> None:
        missing = PutCapableFakeS3Client()
        invalid = PutCapableFakeS3Client()
        invalid.objects[self.key] = (b"jpeg", "image/gif")

        with self.assertRaises(ObjectMissing):
            TemporarySelfieStorage(client=missing).inspect(key=self.key)
        with self.assertRaises(StorageUnavailable) as caught:
            TemporarySelfieStorage(client=invalid).inspect(key=self.key)

        self.assertNotIn(self.key, str(caught.exception))

    def test_delete_is_idempotent_for_one_owned_key(self) -> None:
        client = PutCapableFakeS3Client()
        client.failures["delete_object"] = client_error(404, "NoSuchKey")

        TemporarySelfieStorage(client=client).delete(key=self.key)

        self.assertEqual(client.calls[0][1], {"Bucket": "private-selfies", "Key": self.key})

    def test_grant_is_get_only_for_the_exact_key_and_never_exceeds_ttl(self) -> None:
        client = PutCapableFakeS3Client()

        grant = TemporarySelfieStorage(client=client).create_download_grant(
            key=self.key, max_ttl_seconds=7
        )

        self.assertEqual(grant.url, client.presigned_get_url)
        self.assertEqual(client.calls[0][1]["ExpiresIn"], 7)
        self.assertEqual(
            client.calls[0][1]["Params"], {"Bucket": "private-selfies", "Key": self.key}
        )
        self.assertEqual(client.calls[0][1]["HttpMethod"], "GET")

    def test_sanitizes_storage_failures(self) -> None:
        client = PutCapableFakeS3Client()
        client.failures["put_object"] = client_error(500, message="raw signed secret")

        with self.assertRaises(StorageUnavailable) as caught:
            TemporarySelfieStorage(client=client).put(
                key=self.key, content=b"jpeg", content_type="image/jpeg"
            )

        self.assertNotIn("secret", str(caught.exception))


@override_settings(
    SELFIE_FEEDBACK_S3_BUCKET="private-selfie-feedback",
    SELFIE_FEEDBACK_S3_ACCESS_KEY_ID="feedback-access-key",
    SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="feedback-secret-key",
    SELFIE_FEEDBACK_S3_ENDPOINT_URL="https://storage.yandexcloud.net",
    SELFIE_FEEDBACK_S3_REGION="ru-central1",
    SELFIE_FEEDBACK_KMS_KEY_ID="kms-feedback-key",
    SELFIE_FEEDBACK_MAX_UPLOAD_BYTES=20 * 1024 * 1024,
    SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS=60,
)
class FeedbackSelfieStorageTests(SimpleTestCase):
    """The production breaks caught here are widening staff-only feedback-media access."""

    key = "0123456789abcdef0123456789abcdef"

    def test_put_generates_an_opaque_key_and_uses_private_kms_encrypted_storage(self) -> None:
        client = PutCapableFakeS3Client()

        with patch("selfie_search.storage.secrets.token_hex", return_value=self.key):
            stored = FeedbackSelfieStorage(client=client).put(
                content=b"jpeg", content_type="image/jpeg"
            )

        self.assertEqual(
            (stored.key, stored.size, stored.content_type),
            (self.key, 4, "image/jpeg"),
        )
        self.assertRegex(stored.key, r"^[0-9a-f]{32}$")
        self.assertEqual(
            client.calls,
            [
                (
                    "put_object",
                    {
                        "Bucket": "private-selfie-feedback",
                        "Key": self.key,
                        "Body": b"jpeg",
                        "ContentType": "image/jpeg",
                        "ContentLength": 4,
                        "ACL": "private",
                        "ServerSideEncryption": "aws:kms",
                        "SSEKMSKeyId": "kms-feedback-key",
                    },
                )
            ],
        )

    def test_rejects_invalid_content_and_nonopaque_object_keys(self) -> None:
        storage = FeedbackSelfieStorage(client=PutCapableFakeS3Client())

        with self.assertRaises(ValueError):
            storage.put(content=b"", content_type="image/jpeg")
        with self.assertRaises(ValueError):
            storage.put(content=b"x", content_type="image/gif")
        with self.assertRaises(ValueError):
            storage.put(content=b"x" * (20 * 1024 * 1024 + 1), content_type="image/png")
        for invalid_key in ("feedback/" + self.key, self.key.upper(), "not-a-key"):
            with self.assertRaises(ValueError):
                storage.inspect(key=invalid_key)
            with self.assertRaises(ValueError):
                storage.delete(key=invalid_key)
            with self.assertRaises(ValueError):
                storage.create_download_grant(key=invalid_key)

    def test_inspects_only_a_valid_exact_feedback_object(self) -> None:
        client = PutCapableFakeS3Client()
        client.objects[self.key] = (b"jpeg", "image/png")

        stored = FeedbackSelfieStorage(client=client).inspect(key=self.key)

        self.assertEqual((stored.key, stored.size, stored.content_type), (self.key, 4, "image/png"))
        self.assertEqual(
            client.calls,
            [("head_object", {"Bucket": "private-selfie-feedback", "Key": self.key})],
        )

    def test_missing_feedback_object_is_not_a_storage_incident(self) -> None:
        with self.assertRaises(ObjectMissing):
            FeedbackSelfieStorage(client=PutCapableFakeS3Client()).inspect(key=self.key)

    def test_staff_grant_is_get_only_for_the_exact_key_and_exactly_sixty_seconds(self) -> None:
        client = PutCapableFakeS3Client()

        grant = FeedbackSelfieStorage(client=client).create_download_grant(key=self.key)

        self.assertEqual(grant.url, client.presigned_get_url)
        self.assertEqual(
            client.calls,
            [
                (
                    "generate_presigned_url",
                    {
                        "ClientMethod": "get_object",
                        "Params": {"Bucket": "private-selfie-feedback", "Key": self.key},
                        "ExpiresIn": 60,
                        "HttpMethod": "GET",
                    },
                )
            ],
        )

    def test_sanitizes_sdk_failures_without_exposing_a_key_or_secret(self) -> None:
        client = PutCapableFakeS3Client()
        client.failures["put_object"] = client_error(500, message="raw secret " + self.key)

        with patch("selfie_search.storage.secrets.token_hex", return_value=self.key):
            with self.assertRaises(StorageUnavailable) as caught:
                FeedbackSelfieStorage(client=client).put(content=b"jpeg", content_type="image/jpeg")

        self.assertIsNone(re.search(r"secret|" + self.key, str(caught.exception)))
