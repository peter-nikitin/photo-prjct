from __future__ import annotations

from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.gallery_media_projection import (
    GalleryMediaProjectionConflict,
    publish_gallery_media,
)
from picflow.models import Event, GalleryMediaProjection, Photo


class GalleryMediaProjectionTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-projection-owner")
        self.event = Event.objects.create(
            name="Gallery projection",
            slug="gallery-projection",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"projection-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{suffix}",
            original_filename=f"{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def derivative(
        self,
        photo: Photo,
        *,
        variant: str = "preview-small-v1",
        processor_type: str = GENERATE_PREVIEW_PROCESSOR,
        state_accepts_attempt: bool = True,
        final_key: str | None = None,
    ) -> PhotoDerivative:
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
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
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=processor_type,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt if state_accepts_attempt else None,
            succeeded_at=timezone.now(),
        )
        return PhotoDerivative.objects.create(
            photo=photo,
            variant=variant,
            final_key=final_key
            or f"derivatives/previews/{photo.pk}/{variant}/{uuid4()}-{'a' * 64}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def test_projection_is_sparse_and_uses_the_photo_one_to_one_as_its_identity(self) -> None:
        """The break caught here would create eager rows or allow two projections per photo."""
        photo = self.photo("identity")

        self.assertFalse(GalleryMediaProjection.objects.filter(photo=photo).exists())
        projection = GalleryMediaProjection.objects.create(photo=photo)

        self.assertEqual(projection.pk, photo.pk)
        self.assertTrue(GalleryMediaProjection._meta.get_field("photo").one_to_one)
        self.assertTrue(GalleryMediaProjection._meta.get_field("photo").primary_key)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryMediaProjection.objects.create(photo=photo)

    def test_database_rejects_half_populated_projection_slots(self) -> None:
        """The break caught here would expose a key without provenance or vice versa."""
        photo = self.photo("partial-clean")
        clean = self.derivative(photo)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryMediaProjection.objects.create(
                photo=photo,
                clean_preview_final_key=clean.final_key,
            )

        watermark_photo = self.photo("partial-watermark")
        watermark = self.derivative(
            watermark_photo,
            variant="preview-watermarked-v1",
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryMediaProjection.objects.create(
                photo=watermark_photo,
                watermarked_preview_source_attempt=watermark.accepted_attempt,
            )

    def test_publish_populates_the_clean_slot_from_current_accepted_evidence(self) -> None:
        """The break caught here would omit or misattribute an accepted clean preview."""
        derivative = self.derivative(self.photo("clean"))

        projection = publish_gallery_media(derivative)

        self.assertEqual(projection.photo_id, derivative.photo_id)
        self.assertEqual(projection.clean_preview_final_key, derivative.final_key)
        self.assertEqual(
            projection.clean_preview_source_attempt_id,
            derivative.accepted_attempt_id,
        )
        self.assertIsNone(projection.watermarked_preview_final_key)
        self.assertIsNone(projection.watermarked_preview_source_attempt_id)

    def test_publish_preserves_the_other_projection_slot(self) -> None:
        """The break caught here would erase clean media while adding its watermark."""
        photo = self.photo("both-slots")
        clean = self.derivative(photo)
        watermark = self.derivative(
            photo,
            variant="preview-watermarked-v1",
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )

        publish_gallery_media(clean)
        projection = publish_gallery_media(watermark)

        self.assertEqual(projection.clean_preview_final_key, clean.final_key)
        self.assertEqual(projection.clean_preview_source_attempt_id, clean.accepted_attempt_id)
        self.assertEqual(projection.watermarked_preview_final_key, watermark.final_key)
        self.assertEqual(
            projection.watermarked_preview_source_attempt_id,
            watermark.accepted_attempt_id,
        )

    def test_exact_repeat_is_idempotent_without_touching_the_projection(self) -> None:
        """The break caught here would turn a worker replay into a projection mutation."""
        derivative = self.derivative(self.photo("repeat"))
        original = publish_gallery_media(derivative)

        repeated = publish_gallery_media(derivative)

        self.assertEqual(repeated.pk, original.pk)
        self.assertEqual(repeated.updated_at, original.updated_at)
        self.assertEqual(
            GalleryMediaProjection.objects.filter(photo_id=derivative.photo_id).count(),
            1,
        )

    def test_different_value_in_an_occupied_slot_is_a_conflict(self) -> None:
        """The break caught here would silently replace immutable accepted media history."""
        derivative = self.derivative(self.photo("conflict"))
        occupied_key = "derivatives/previews/already-published.jpg"
        projection = GalleryMediaProjection.objects.create(
            photo_id=derivative.photo_id,
            clean_preview_final_key=occupied_key,
            clean_preview_source_attempt=derivative.accepted_attempt,
        )

        with self.assertRaises(GalleryMediaProjectionConflict):
            publish_gallery_media(derivative)

        projection.refresh_from_db()
        self.assertEqual(projection.clean_preview_final_key, occupied_key)

    def test_publish_rejects_unsupported_or_wrongly_attributed_derivatives(self) -> None:
        """The break caught here would project a derivative from the wrong media producer."""
        unsupported_photo = self.photo("unsupported")
        unsupported = self.derivative(
            unsupported_photo,
        )
        unsupported.variant = "thumbnail-v1"
        with self.assertRaises(ValueError):
            publish_gallery_media(unsupported)

        wrong_processor_photo = self.photo("wrong-processor")
        wrong_processor = self.derivative(wrong_processor_photo)
        wrong_processor.accepted_attempt.processor_type = GENERATE_WATERMARKED_PREVIEW_PROCESSOR
        with self.assertRaises(ValueError):
            publish_gallery_media(wrong_processor)

        other_photo = self.photo("other-photo")
        wrong_photo = self.derivative(self.photo("wrong-photo"))
        wrong_photo.accepted_attempt.photo_id = other_photo.pk
        with self.assertRaises(ValueError):
            publish_gallery_media(wrong_photo)

    def test_publish_rejects_nonaccepted_or_noncurrent_evidence(self) -> None:
        """The break caught here would publish stale or unaccepted processing evidence."""
        in_progress = self.derivative(self.photo("in-progress"))
        in_progress.accepted_attempt.status = ProcessingAttempt.Status.IN_PROGRESS
        with self.assertRaises(ValueError):
            publish_gallery_media(in_progress)

        unaccepted = self.derivative(self.photo("unaccepted"))
        unaccepted.accepted_attempt.accepted = False
        with self.assertRaises(ValueError):
            publish_gallery_media(unaccepted)

        noncurrent = self.derivative(
            self.photo("noncurrent"),
            state_accepts_attempt=False,
        )
        with self.assertRaises(ValueError):
            publish_gallery_media(noncurrent)
