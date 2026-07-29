from django.test import SimpleTestCase, override_settings
from ingestion.storage import ObjectMissing, StorageUnavailable
from ingestion.tests.fakes import FakeS3Client, client_error

from processing.storage import ExactObjectDownloadStorage


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
