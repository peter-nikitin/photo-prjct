from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.gallery import (
    gallery_photo_queryset,
    purchasable_paid_photo_queryset,
    saved_result_photo_queryset,
)
from picflow.models import Event, Photo

type ChoiceValue = str | tuple[str, str]


class PhotoVisibilityTests(TestCase):
    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="visibility-owner")

    def make_event(self, *, slug: str, access_type: ChoiceValue = Event.AccessType.FREE) -> Event:
        return Event.objects.create(
            name=slug.replace("-", " ").title(),
            slug=slug,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 7),
            city="Moscow",
            timezone_name="Europe/Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=access_type,
            price_per_photo_kopecks=(30000 if access_type == Event.AccessType.PAID else None),
        )

    def make_photo(self, event: Event, *, photo_id: str, watermarked: bool = False) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=event,
            src="",
            uploaded_by=self.owner,
            original_key=f"originals/{photo_id}.jpg",
            original_filename=f"{photo_id}.jpg",
            original_size=123,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=(
                Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
                if watermarked
                else Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1
            ),
            gallery_media_policy=(
                Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
                if watermarked
                else Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED
            ),
        )

    def publish_watermark(self, photo: Photo) -> None:
        configuration = {
            GENERATE_WATERMARKED_PREVIEW_PROCESSOR: {"variant": "preview-watermarked-v1"}
        }
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=timezone.now(),
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-watermarked-v1",
            final_key=f"derivatives/previews/{photo.pk}/preview-watermarked-v1/{uuid4().hex}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="b" * 64,
            accepted_attempt=attempt,
        )

    def test_hide_and_show_suppresses_free_public_access_without_mutating_media_or_worker_state(
        self,
    ) -> None:
        event = self.make_event(slug="free-visibility")
        photo = self.make_photo(event, photo_id="free-visible")
        state = PhotoProcessingState.objects.create(
            photo=photo,
            processor_type="face_embedding",
            status=PhotoProcessingState.Status.NOT_REQUESTED,
        )
        immutable_values = (
            photo.original_key,
            photo.original_filename,
            photo.original_size,
            photo.original_content_type,
            photo.processing_generation,
            photo.gallery_media_policy,
        )
        media_url = reverse(
            "photo_media",
            kwargs={"slug": event.slug, "photo_id": photo.pk, "variant": "preview-small"},
        )
        download_url = reverse("photo_download", kwargs={"slug": event.slug, "photo_id": photo.pk})

        Photo.objects.filter(pk=photo.pk).update(is_hidden=True)
        with patch("config.views._public_media_resolver") as resolver_factory:
            resolver_factory.return_value.resolve_signed.return_value = (
                "https://storage.example.test/hidden-preview"
            )
            resolver_factory.return_value.resolve_download.return_value = (
                "https://storage.example.test/hidden-original"
            )
            hidden_media = self.client.get(media_url)
            hidden_download = self.client.get(download_url)

        self.assertEqual(list(gallery_photo_queryset(event=event)), [])
        self.assertEqual(
            list(saved_result_photo_queryset(event=event, paid_watermarked_previews_enabled=False)),
            [],
        )
        self.assertEqual((hidden_media.status_code, hidden_download.status_code), (404, 404))
        resolver_factory.assert_not_called()

        Photo.objects.filter(pk=photo.pk).update(is_hidden=False)
        photo.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(list(gallery_photo_queryset(event=event)), [photo])
        self.assertEqual(
            list(saved_result_photo_queryset(event=event, paid_watermarked_previews_enabled=False)),
            [photo],
        )
        self.assertEqual(
            (
                photo.original_key,
                photo.original_filename,
                photo.original_size,
                photo.original_content_type,
                photo.processing_generation,
                photo.gallery_media_policy,
            ),
            immutable_values,
        )
        self.assertEqual(state.status, PhotoProcessingState.Status.NOT_REQUESTED)

    def test_hidden_watermarked_paid_photo_is_not_public_or_purchasable_until_shown(self) -> None:
        event = self.make_event(slug="paid-visibility", access_type=Event.AccessType.PAID)
        photo = self.make_photo(event, photo_id="paid-watermarked", watermarked=True)
        self.publish_watermark(photo)

        photo.is_hidden = True
        photo.save(update_fields=["is_hidden"])

        self.assertEqual(
            list(gallery_photo_queryset(event=event, paid_watermarked_previews_enabled=True)), []
        )
        self.assertEqual(
            list(purchasable_paid_photo_queryset(event=event, watermarked_previews_enabled=True)),
            [],
        )

        photo.is_hidden = False
        photo.save(update_fields=["is_hidden"])

        self.assertEqual(
            list(gallery_photo_queryset(event=event, paid_watermarked_previews_enabled=True)),
            [photo],
        )
        self.assertEqual(
            list(purchasable_paid_photo_queryset(event=event, watermarked_previews_enabled=True)),
            [photo],
        )
