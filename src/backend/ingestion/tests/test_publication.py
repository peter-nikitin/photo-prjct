from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from feature_flags.registry import PAID_WATERMARKED_PREVIEWS
from feature_flags.states import FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.services.publication import VerifiedOriginal, publish_photo
from ingestion.storage import ObjectIdentity
from picflow.models import Event, EventFolder
from processing.models import PhotoProcessingState


class PublicationTests(TransactionTestCase):
    def setUp(self) -> None:
        self.uploader = get_user_model().objects.create_user(username="publication-photographer")
        self.event = Event.objects.create(
            name="Publication",
            slug="publication",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        self.folder = EventFolder.objects.create(event=self.event, name="Finish")
        self.original = VerifiedOriginal(
            key="originals/publication/photo.jpg",
            identity=ObjectIdentity(
                etag_wire='"verified-etag"',
                etag_value="verified-etag",
                size=1_024,
                content_type="image/jpeg",
            ),
            filename="finish.jpg",
            oriented_geometry=(1_200, 800),
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_publish_photo_applies_shared_policy_and_standard_enrollment(self) -> None:
        with transaction.atomic():
            photo = publish_photo(
                photo_id="published-photo",
                uploader=self.uploader,
                event=self.event,
                folder=self.folder,
                original=self.original,
            )

        self.assertEqual(photo.pk, "published-photo")
        self.assertEqual(photo.event, self.event)
        self.assertEqual(photo.folder, self.folder)
        self.assertEqual(photo.uploaded_by, self.uploader)
        self.assertEqual(photo.src.name, "")
        self.assertEqual(photo.original_key, "originals/publication/photo.jpg")
        self.assertEqual(photo.original_filename, "finish.jpg")
        self.assertEqual(photo.original_size, 1_024)
        self.assertEqual(photo.original_content_type, "image/jpeg")
        self.assertIsNotNone(photo.uploaded_at)
        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            ("preview_first_v1", "preview_required"),
        )
        capture = PhotoProcessingState.objects.get(
            photo=photo,
            processor_type="capture_metadata",
        )
        self.assertEqual(capture.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(
            capture.current_job.input_fingerprint,
            {
                "original_key": "originals/publication/photo.jpg",
                "original_size": 1_024,
                "original_content_type": "image/jpeg",
                "verified_source_etag": "verified-etag",
                "version_evidence": "verified_source_etag",
            },
        )
        preview = PhotoProcessingState.objects.get(
            photo=photo,
            processor_type="generate_preview",
        )
        self.assertEqual(preview.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(preview.current_job.input_fingerprint["pixel_width"], 1_200)
        self.assertEqual(preview.current_job.input_fingerprint["pixel_height"], 800)

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_publish_photo_uses_paid_policy_without_changing_enrollment_identity(self) -> None:
        Event.objects.filter(pk=self.event.pk).update(
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30_000,
        )
        self.event.refresh_from_db()

        with override_feature_flags({PAID_WATERMARKED_PREVIEWS: FEATURE_FLAG_ON}):
            with transaction.atomic():
                photo = publish_photo(
                    photo_id="paid-photo",
                    uploader=self.uploader,
                    event=self.event,
                    folder=None,
                    original=self.original,
                )

        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            ("preview_first_watermarked_v1", "watermarked_preview_required"),
        )
        preview = PhotoProcessingState.objects.get(
            photo=photo,
            processor_type="generate_preview",
        )
        self.assertEqual(preview.current_job.contract_version, 2)
        self.assertEqual(preview.current_job.processor_version, 1)
