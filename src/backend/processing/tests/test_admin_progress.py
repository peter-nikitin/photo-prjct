from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import EventProcessingRun, ProcessingJob


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class AdminProcessingProgressTests(TestCase):
    def setUp(self) -> None:
        self.staff = get_user_model().objects.create_user(
            username="processing-staff", password="password", is_staff=True
        )
        self.ordinary_user = get_user_model().objects.create_user(
            username="processing-user", password="password"
        )
        self.event = Event.objects.create(
            name="Processing event",
            slug="processing-event",
            start_date=date(2026, 7, 31),
            end_date=date(2026, 7, 31),
            city="Moscow",
        )

    def create_run(self, processor_type: str) -> EventProcessingRun:
        return EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type=processor_type,
            processor_version=1,
            configuration={},
            configuration_hash=(processor_type[0] * 64),
            status="collecting",
        )

    def create_photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"progress-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.ordinary_user,
            original_key=f"originals/progress-{suffix}.jpg",
            original_filename=f"progress-{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def create_job(
        self,
        run: EventProcessingRun,
        photo: Photo,
        *,
        status: str,
        claimed_at: datetime | None = None,
    ) -> ProcessingJob:
        return ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type=run.processor_type,
            processor_version=run.processor_version,
            configuration=run.configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=status,
            claimed_at=claimed_at,
        )

    def test_requires_staff_access(self) -> None:
        self.client.force_login(self.ordinary_user)

        response = self.client.get(reverse("admin_processing_progress"))

        self.assertRedirects(response, f"{reverse('admin:login')}?next=/admin/processing/")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin_processing_progress")).status_code, 200)

    def test_displays_one_event_row_with_common_distinct_photo_total(self) -> None:
        preview = self.create_run("generate_preview")
        watermark = self.create_run("generate_watermarked_preview")
        embedding = self.create_run("face_embedding")
        metadata = self.create_run("capture_metadata")
        first, second = self.create_photo("first"), self.create_photo("second")
        self.create_job(preview, first, status="succeeded")
        self.create_job(watermark, first, status="succeeded")
        self.create_job(embedding, first, status="succeeded")
        self.create_job(metadata, first, status="succeeded")
        self.create_job(preview, second, status="queued")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_processing_progress"))

        self.assertContains(response, "Processing event", count=1)
        self.assertContains(response, "Total photos: 2")
        self.assertContains(response, "Preview")
        self.assertContains(response, "Watermark")
        self.assertContains(response, "Embedding")
        self.assertContains(response, "Metadata")
        self.assertContains(response, "In progress")

    def test_marks_event_completed_only_when_every_worker_completed_common_total(self) -> None:
        photos = [self.create_photo(suffix) for suffix in ("one", "two")]
        for processor_type in (
            "generate_preview",
            "generate_watermarked_preview",
            "face_embedding",
            "capture_metadata",
        ):
            run = self.create_run(processor_type)
            for photo in photos:
                self.create_job(run, photo, status="succeeded")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_processing_progress"))

        row = response.context["rows"][0]
        self.assertEqual(row["status"], "Completed")
        self.assertEqual([worker["status"] for worker in row["workers"]], ["Completed"] * 4)

    def test_free_event_without_watermark_jobs_can_complete(self) -> None:
        photos = [self.create_photo(suffix) for suffix in ("free-one", "free-two")]
        for processor_type in ("generate_preview", "face_embedding", "capture_metadata"):
            run = self.create_run(processor_type)
            for photo in photos:
                self.create_job(run, photo, status="succeeded")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_processing_progress"))

        row = response.context["rows"][0]
        watermark = next(worker for worker in row["workers"] if worker["name"] == "Watermark")
        self.assertEqual(row["status"], "Completed")
        self.assertEqual(watermark["total"], 0)
        self.assertEqual(watermark["status"], "Not applicable")

    def test_mixed_paid_event_counts_only_explicitly_enrolled_watermark_photos(self) -> None:
        self.event.access_type = Event.AccessType.PAID
        self.event.save(update_fields=["access_type"])
        old_photo = self.create_photo("paid-old")
        new_photo = self.create_photo("paid-new")
        new_photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        new_photo.gallery_media_policy = Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        new_photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        for processor_type in ("generate_preview", "face_embedding", "capture_metadata"):
            run = self.create_run(processor_type)
            for photo in (old_photo, new_photo):
                self.create_job(run, photo, status="succeeded")
        watermark_run = self.create_run("generate_watermarked_preview")
        self.create_job(watermark_run, new_photo, status="succeeded")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_processing_progress"))

        row = response.context["rows"][0]
        watermark = next(worker for worker in row["workers"] if worker["name"] == "Watermark")
        self.assertEqual(row["status"], "Completed")
        self.assertEqual(watermark["total"], 1)
        self.assertEqual(watermark["completed"], 1)
        self.assertEqual(watermark["status"], "Completed")

    def test_displays_worker_own_eta_for_one_claimed_job(self) -> None:
        now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        preview = self.create_run("generate_preview")
        first, second = self.create_photo("first"), self.create_photo("second")
        self.create_job(preview, first, status="processing", claimed_at=now - timedelta(minutes=10))
        self.create_job(preview, second, status="queued")
        self.client.force_login(self.staff)

        with patch("processing.admin_progress.timezone.now", return_value=now):
            response = self.client.get(reverse("admin_processing_progress"))

        self.assertContains(response, "0 / 2")
        self.assertContains(response, "Processing")
        self.assertContains(response, "2026-07-31 12:20 UTC")

    def test_embedding_waits_for_incomplete_preview_when_it_has_no_work(self) -> None:
        preview = self.create_run("generate_preview")
        photo = self.create_photo("waiting")
        self.create_job(preview, photo, status="queued")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_processing_progress"))

        self.assertContains(response, "Waiting for preview", count=2)
