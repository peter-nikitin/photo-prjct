import hashlib

from botocore.exceptions import ReadTimeoutError, ResponseStreamingError
from django.test import SimpleTestCase, override_settings
from ingestion.storage import ObjectMissing, StorageUnavailable
from ingestion.tests.fakes import FakeS3Client, client_error

from processing.storage import ExactObjectDownloadStorage, ExactPreviewStorage, ObjectConflict

# This deliberately small JPEG has an SOF0 frame declaring a 1x1 image.  The
# storage boundary must derive dimensions while hashing the object stream,
# rather than trusting the worker's completion payload.
JPEG_1X1 = bytes.fromhex(
    "ffd8"
    "ffe000104a46494600010100000100010000"
    "ffc00011080001000103011100021100031100"
    "ffda000c03010002110311003f00"
    "ffd9"
)


@override_settings(
    PRIVATE_MEDIA_S3_BUCKET="private-photos",
    PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=90,
)
class ExactObjectDownloadStorageTests(SimpleTestCase):
    """The production break caught here is widening a download grant beyond its final key."""

    def test_creates_a_short_lived_get_grant_for_one_exact_final_key(self) -> None:
        client = FakeS3Client()
        client.presigned_get_url = (
            "https://storage.example.test/originals/0123456789abcdef0123456789abcdef?secret"
        )

        grant = ExactObjectDownloadStorage(client=client).create_download_grant(
            final_key="originals/0123456789abcdef0123456789abcdef"
        )

        self.assertEqual(grant.url, client.presigned_get_url)
        self.assertGreater(grant.expires_at.timestamp(), 0)
        self.assertEqual(
            client.calls,
            [
                (
                    "generate_presigned_url",
                    {
                        "ClientMethod": "get_object",
                        "Params": {
                            "Bucket": "private-photos",
                            "Key": "originals/0123456789abcdef0123456789abcdef",
                            "ResponseContentType": "image/jpeg",
                            "ResponseContentDisposition": 'attachment; filename="photo.jpg"',
                        },
                        "ExpiresIn": 90,
                        "HttpMethod": "GET",
                    },
                )
            ],
        )

    def test_caps_grant_expiry_to_the_positive_remaining_lease(self) -> None:
        client = FakeS3Client()

        ExactObjectDownloadStorage(client=client).create_download_grant(
            final_key="originals/0123456789abcdef0123456789abcdef", max_ttl_seconds=7
        )

        self.assertEqual(client.calls[0][1]["ExpiresIn"], 7)

    def test_rejects_a_key_outside_the_application_owned_final_namespace(self) -> None:
        with self.assertRaises(ValueError):
            ExactObjectDownloadStorage(client=FakeS3Client()).create_download_grant(
                final_key="other-event/private.jpg"
            )

    def test_maps_missing_and_unavailable_s3_responses_without_exposing_a_url(self) -> None:
        missing = FakeS3Client()
        missing.failures["generate_presigned_url"] = client_error(404, "NoSuchKey")
        unavailable = FakeS3Client()
        unavailable.failures["generate_presigned_url"] = client_error(500, "InternalError")

        with self.assertRaises(ObjectMissing) as missing_error:
            ExactObjectDownloadStorage(client=missing).create_download_grant(
                final_key="originals/0123456789abcdef0123456789abcdef"
            )
        with self.assertRaises(StorageUnavailable) as unavailable_error:
            ExactObjectDownloadStorage(client=unavailable).create_download_grant(
                final_key="originals/0123456789abcdef0123456789abcdef"
            )

        self.assertNotIn("secret", str(missing_error.exception))
        self.assertNotIn("secret", str(unavailable_error.exception))


@override_settings(
    PRIVATE_MEDIA_S3_BUCKET="private-photos",
    PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=90,
)
class ExactPreviewStorageTests(SimpleTestCase):
    """The production breaks caught here grant a worker a broader preview write or trust it."""

    staging_key = (
        "processing-staging/previews/01234567-89ab-cdef-0123-456789abcdef/preview-small-v1.jpg"
    )
    final_key = (
        "derivatives/previews/photo_1/preview-small-v1/"
        "01234567-89ab-cdef-0123-456789abcdef-"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
    )

    def test_creates_a_lease_bounded_get_grant_for_one_accepted_preview(self) -> None:
        client = FakeS3Client()
        client.presigned_get_url = "https://storage.example.test/preview?secret"

        grant = ExactPreviewStorage(client=client).create_download_grant(
            final_key=self.final_key, max_ttl_seconds=7
        )

        self.assertEqual(grant.url, client.presigned_get_url)
        self.assertEqual(
            client.calls,
            [
                (
                    "generate_presigned_url",
                    {
                        "ClientMethod": "get_object",
                        "Params": {
                            "Bucket": "private-photos",
                            "Key": self.final_key,
                            "ResponseContentType": "image/jpeg",
                            "ResponseContentDisposition": 'attachment; filename="preview.jpg"',
                        },
                        "ExpiresIn": 7,
                        "HttpMethod": "GET",
                    },
                )
            ],
        )

    def test_creates_a_lease_bounded_put_grant_for_one_exact_staging_key(self) -> None:
        client = FakeS3Client()
        client.presigned_get_url = "https://storage.example.test/staging?secret"

        grant = ExactPreviewStorage(client=client).create_upload_grant(
            staging_key=self.staging_key, max_ttl_seconds=7
        )

        self.assertEqual(grant.url, "https://storage.example.test/staging?secret")
        self.assertGreater(grant.expires_at.timestamp(), 0)
        self.assertEqual(
            client.calls,
            [
                (
                    "generate_presigned_url",
                    {
                        "ClientMethod": "put_object",
                        "Params": {
                            "Bucket": "private-photos",
                            "Key": self.staging_key,
                            "ContentType": "image/jpeg",
                        },
                        "ExpiresIn": 7,
                        "HttpMethod": "PUT",
                    },
                )
            ],
        )

    def test_rejects_any_key_outside_the_two_preview_namespaces(self) -> None:
        storage = ExactPreviewStorage(client=FakeS3Client())

        with self.assertRaises(ValueError):
            storage.create_upload_grant(
                staging_key="processing-staging/previews/other.jpg", max_ttl_seconds=1
            )
        with self.assertRaises(ValueError):
            storage.verify(key="originals/0123456789abcdef0123456789abcdef", max_bytes=10)
        with self.assertRaises(ValueError):
            storage.promote(
                staging_key=self.staging_key,
                final_key=(
                    "derivatives/previews/photo_1/preview-small-v1/"
                    "01234567-89ab-cdef-0123-456789abcdef-not-a-checksum.jpg"
                ),
                source_etag='"etag"',
            )

    def test_streams_checksum_and_dimensions_from_the_verified_staging_object(self) -> None:
        client = FakeS3Client()
        client.put_object(self.staging_key, JPEG_1X1, '"preview-etag"')

        verified = ExactPreviewStorage(client=client).verify(
            key=self.staging_key, max_bytes=len(JPEG_1X1)
        )

        self.assertEqual(verified.byte_size, len(JPEG_1X1))
        self.assertEqual(verified.content_type, "image/jpeg")
        self.assertEqual(verified.sha256, hashlib.sha256(JPEG_1X1).hexdigest())
        self.assertEqual((verified.width, verified.height), (1, 1))
        assert client.last_body is not None
        self.assertTrue(client.last_body.closed)
        self.assertEqual(client.calls[1][1]["IfMatch"], '"preview-etag"')

    def test_promotion_conditions_the_source_and_rejects_a_final_key_seen_before_copy(
        self,
    ) -> None:
        client = FakeS3Client()
        client.put_object(self.staging_key, JPEG_1X1, '"source-etag"')
        storage = ExactPreviewStorage(client=client)

        promoted = storage.promote(
            staging_key=self.staging_key,
            final_key=self.final_key,
            source_etag='"source-etag"',
        )

        self.assertEqual(promoted.etag_wire, '"source-etag"')
        copy = next(call for call in client.calls if call[0] == "copy_object")
        self.assertEqual(copy[1]["CopySourceIfMatch"], '"source-etag"')
        self.assertNotIn("IfNoneMatch", copy[1])

        client.put_object(self.final_key, b"other", '"final-etag"')
        with self.assertRaises(ObjectConflict):
            storage.promote(
                staging_key=self.staging_key,
                final_key=self.final_key,
                source_etag='"source-etag"',
            )
        self.assertEqual(client.objects[self.final_key].content, b"other")

    def test_maps_storage_failures_to_sanitized_errors(self) -> None:
        client = FakeS3Client()
        client.failures["head_object"] = client_error(500, message="https://secret.example.test")

        with self.assertRaises(StorageUnavailable) as error:
            ExactPreviewStorage(client=client).verify(key=self.staging_key, max_bytes=10)

        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("https", str(error.exception))

    def test_maps_streaming_read_timeout_to_a_sanitized_retryable_storage_error(self) -> None:
        client = FakeS3Client()
        client.put_object(self.staging_key, JPEG_1X1, '"preview-etag"')

        class TimeoutBody:
            closed = False

            def read(self, amt: int | None = None) -> bytes:
                raise ReadTimeoutError(
                    endpoint_url="https://secret-storage.example.test",
                    error=TimeoutError("secret body failure"),
                )

            def close(self) -> None:
                self.closed = True

        timeout_body = TimeoutBody()
        original_get = client.get_object

        def get_object(**kwargs):
            response = original_get(**kwargs)
            response["Body"] = timeout_body
            return response

        client.get_object = get_object  # type: ignore[method-assign]

        with self.assertRaises(StorageUnavailable) as error:
            ExactPreviewStorage(client=client).verify(key=self.staging_key, max_bytes=len(JPEG_1X1))

        self.assertTrue(timeout_body.closed)
        self.assertNotIn("secret", str(error.exception))

    def test_maps_response_streaming_error_to_a_sanitized_retryable_storage_error(self) -> None:
        client = FakeS3Client()
        client.put_object(self.staging_key, JPEG_1X1, '"preview-etag"')

        class StreamingFailureBody:
            closed = False

            def read(self, amt: int | None = None) -> bytes:
                raise ResponseStreamingError(
                    error=TimeoutError("secret body failure"), operation_name="GetObject"
                )

            def close(self) -> None:
                self.closed = True

        failing_body = StreamingFailureBody()
        original_get = client.get_object

        def get_object(**kwargs):
            response = original_get(**kwargs)
            response["Body"] = failing_body
            return response

        client.get_object = get_object  # type: ignore[method-assign]

        with self.assertRaises(StorageUnavailable) as error:
            ExactPreviewStorage(client=client).verify(key=self.staging_key, max_bytes=len(JPEG_1X1))

        self.assertTrue(failing_body.closed)
        self.assertNotIn("secret", str(error.exception))
