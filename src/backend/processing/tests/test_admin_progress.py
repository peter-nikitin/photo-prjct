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

    def create_run(self, *, status: str = "collecting") -> EventProcessingRun:
        return EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
            status=status,
        )

    def create_job(
        self,
        run: EventProcessingRun,
        suffix: str,
        *,
        status: str,
        claimed_at: datetime | None = None,
    ) -> ProcessingJob:
        photo = Photo.objects.create(
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

    def test_displays_current_counts_and_finish_estimate(self) -> None:
        now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        run = self.create_run()
        for suffix in ("queued-one", "queued-two", "queued-three"):
            self.create_job(run, suffix, status="queued")
        self.create_job(
            run,
            "processing",
            status="processing",
            claimed_at=now - timedelta(minutes=10),
        )
        for suffix in ("retry-one", "retry-two"):
            self.create_job(run, suffix, status="retry_wait")
        for suffix in ("succeeded-one", "succeeded-two"):
            self.create_job(run, suffix, status="succeeded")
        self.create_job(run, "failed", status="failed")
        self.create_job(run, "cancelled", status="cancelled")
        self.client.force_login(self.staff)

        with patch("processing.admin_progress.timezone.now", return_value=now):
            response = self.client.get(reverse("admin_processing_progress"))

        self.assertContains(response, "Processing event")
        self.assertContains(response, "capture_metadata v1")
        self.assertContains(response, "collecting")
        self.assertContains(response, "Total: 10")
        for status, count in {
            "queued": 3,
            "processing": 1,
            "retry_wait": 2,
            "succeeded": 2,
            "failed": 1,
            "cancelled": 1,
        }.items():
            self.assertContains(response, f"{status}: {count}")
        self.assertContains(response, "Processed: 4")
        self.assertContains(response, "Remaining: 6")
        self.assertContains(response, "2026-07-31 13:00 UTC")

    def test_displays_unavailable_or_completed_eta(self) -> None:
        queued_run = self.create_run()
        self.create_job(queued_run, "queued-only", status="queued")
        closed_run = self.create_run()
        self.create_job(closed_run, "closed", status="succeeded")
        EventProcessingRun.objects.filter(pk=closed_run.pk).update(status="closed")
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_processing_progress"))

        self.assertContains(response, "—")
        self.assertContains(response, "Completed")
