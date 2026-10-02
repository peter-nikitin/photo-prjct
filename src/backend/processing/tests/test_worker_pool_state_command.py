import json
from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from picflow.models import Event, Photo
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchJob,
    SelfieSearchResult,
)

from processing.models import (
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)


@override_settings(PHOTO_PROCESSING_ENABLED=True, PHOTO_PROCESSING_FLEET_TOKEN="private-token")
class WorkerPoolStateCommandTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.event = Event.objects.create(
            name="private event",
            slug="private-event",
            start_date=date.today(),
            end_date=date.today(),
        )
        self.photo = Photo.objects.create(
            id="private-photo", event=self.event, src="https://private-url/photo.jpg"
        )

    def bulk_job(
        self,
        status="queued",
        *,
        due=True,
        identity=(1, "capture_metadata", 2),
        current=True,
        closed=False,
        photo=None,
    ):
        contract, processor, version = identity
        photo = photo or self.photo
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=contract,
            processor_type=processor,
            processor_version=version,
            configuration_hash="a" * 64,
            status="collecting",
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            photo=photo,
            run=run,
            contract_version=contract,
            processor_type=processor,
            processor_version=version,
            configuration_hash="a" * 64,
            status=status,
            available_at=self.now + timedelta(hours=1 if not due else -1),
        )
        if closed:
            EventProcessingRun.objects.filter(pk=run.pk).update(status="closed")
        if current:
            PhotoProcessingState.objects.update_or_create(
                photo=photo,
                processor_type=processor,
                defaults={
                    "current_run": run,
                    "current_job": job,
                    "current_attempt": None,
                    "status": status,
                },
            )
        return job

    def bulk_attempt(self, job, *, status="in_progress", expired=False, accepted=False):
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            photo=job.photo,
            run=job.run,
            job=job,
            contract_version=job.contract_version,
            processor_type=job.processor_type,
            processor_version=job.processor_version,
            status=status,
            accepted=accepted,
            terminal_at=None if status == "in_progress" else self.now,
            lease_expires_at=self.now + timedelta(hours=-1 if expired else 1),
            result={"secret": "https://private-url/private-key"},
        )
        PhotoProcessingState.objects.filter(current_job=job).update(current_attempt=attempt)
        return attempt

    def selfie_job(
        self, status="queued", *, due=True, search_status="queued", key="private-object-key"
    ):
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=f"{SelfieSearch.objects.count():064d}",
            temporary_object_key=key,
            status=search_status,
        )
        return SelfieSearchJob.objects.create(
            search=search,
            status=status,
            available_at=self.now + timedelta(hours=1 if not due else -1),
        )

    def report(self, **kwargs):
        output = StringIO()
        call_command("report_worker_pool_state", "--json", stdout=output, **kwargs)
        return json.loads(output.getvalue())

    def test_empty_report_is_explicit_and_bounded(self):
        report = self.report()
        self.assertTrue(report["empty"])
        self.assertEqual(len(report["pools"]["bulk"]["identities"]), 6)
        self.assertEqual(len(report["pools"]["selfie"]["identities"]), 1)
        self.assertEqual(report["pools"]["bulk"]["jobs"]["queued"], 0)
        self.assertEqual(report["pools"]["selfie"]["claimable"], 0)

    def test_unenrolled_historical_photos_are_not_worker_demand(self):
        from django.contrib.auth import get_user_model

        from processing.services.historical_adaface import historical_adaface_status

        self.event.face_search_generation = Event.FaceSearchGeneration.SFACE_V3
        self.event.save(update_fields=["face_search_generation"])
        self.photo.src = ""
        self.photo.original_key = "private/unenrolled.jpg"
        self.photo.original_size = 1
        self.photo.original_filename = "unenrolled.jpg"
        self.photo.original_content_type = "image/jpeg"
        self.photo.uploaded_by = get_user_model().objects.create_user(username="unenrolled-owner")
        self.photo.uploaded_at = timezone.now()
        self.photo.save()
        status = historical_adaface_status(self.event)
        self.assertEqual(status["not_enrolled_count"], 1)
        bulk = self.report()["pools"]["bulk"]
        self.assertEqual(bulk["claimable"], 0)
        self.assertEqual(bulk["leases"]["active"], 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)

    def test_bulk_counts_current_due_jobs_open_runs_and_preserves_all_rows(self):
        self.bulk_job(current=False)
        closed_photo = Photo.objects.create(id="closed-photo", event=self.event, src="/closed.jpg")
        future_photo = Photo.objects.create(id="future-photo", event=self.event, src="/future.jpg")
        self.bulk_job(closed=True, photo=closed_photo)
        self.bulk_job("retry_wait", due=False, photo=future_photo)
        self.bulk_job("retry_wait")
        self.bulk_job(identity=(9, "unknown-private-processor", 9), current=False)
        before = list(ProcessingJob.objects.values())
        report = self.report()
        bulk = report["pools"]["bulk"]
        self.assertEqual(
            bulk["jobs"],
            {
                "queued": 2,
                "processing": 0,
                "retry_wait": 2,
                "succeeded": 0,
                "failed": 0,
                "cancelled": 0,
            },
        )
        self.assertEqual(bulk["claimable"], 1)
        self.assertEqual(bulk["retries"], {"due": 1, "future": 1})
        self.assertEqual(report["unassigned_processing_jobs"], 1)
        self.assertEqual(list(ProcessingJob.objects.values()), before)
        self.assertNotIn("unknown-private-processor", json.dumps(report))

    def test_report_selects_only_and_counts_published_derivatives_without_mutation(self):
        job = self.bulk_job("succeeded", identity=(2, "generate_preview", 1))
        accepted = self.bulk_attempt(job, status="succeeded", accepted=True)
        PhotoProcessingState.objects.filter(current_job=job).update(accepted_attempt=accepted)
        derivative = PhotoDerivative.objects.create(
            photo=self.photo,
            variant="preview-small-v1",
            final_key="private-derivative-key",
            byte_size=10,
            content_type="image/jpeg",
            width=20,
            height=20,
            oriented_source_width=20,
            oriented_source_height=20,
            sha256="a" * 64,
            accepted_attempt=accepted,
        )
        before = list(PhotoDerivative.objects.values())
        with CaptureQueriesContext(connection) as queries:
            report = self.report()
        self.assertTrue(all(query["sql"].lstrip().startswith("SELECT") for query in queries))
        self.assertEqual(report["pools"]["bulk"]["artifacts"]["derivatives"], 1)
        self.assertEqual(report["pools"]["bulk"]["artifacts"]["current_accepted_attempts"], 1)
        self.assertNotIn(derivative.final_key, json.dumps(report))
        self.assertEqual(list(PhotoDerivative.objects.values()), before)

    def test_selfie_store_counts_due_retries_and_search_dependencies(self):
        self.selfie_job()
        self.selfie_job("retry_wait")
        self.selfie_job("retry_wait", due=False)
        self.selfie_job(key="")
        self.selfie_job(search_status="failed")
        complete = self.selfie_job("succeeded", search_status="ready")
        SelfieSearchResult.objects.create(search=complete.search, photo=self.photo, rank=1)
        SelfieSearchAttempt.objects.create(job=complete, status="succeeded", terminal_at=self.now)
        before = list(SelfieSearch.objects.values())
        selfie = self.report()["pools"]["selfie"]
        self.assertEqual(
            selfie["jobs"],
            {"queued": 3, "processing": 0, "retry_wait": 2, "succeeded": 1, "failed": 0},
        )
        self.assertEqual(selfie["claimable"], 2)
        self.assertEqual(selfie["retries"], {"due": 1, "future": 1})
        self.assertEqual(selfie["artifacts"]["results"], 1)
        self.assertEqual(selfie["attempts"]["succeeded"], 1)
        self.assertEqual(list(SelfieSearch.objects.values()), before)

    def test_current_leases_and_accepted_artifacts_are_counted_without_recovery(self):
        active = self.bulk_job("processing")
        self.bulk_attempt(active)
        expired = self.bulk_job("processing", identity=(2, "face_embedding", 3))
        expired_attempt = self.bulk_attempt(expired, expired=True)
        accepted_photo = Photo.objects.create(
            id="accepted-photo", event=self.event, src="https://private-url/accepted.jpg"
        )
        accepted_job = self.bulk_job(
            "succeeded", identity=(3, "face_embedding", 5), photo=accepted_photo
        )
        accepted = self.bulk_attempt(accepted_job, status="succeeded", accepted=True)
        FaceProcessingAttemptArtifact.objects.create(attempt=accepted)
        selfie = self.selfie_job("processing", search_status="processing")
        SelfieSearchAttempt.objects.create(
            job=selfie, lease_expires_at=self.now - timedelta(hours=1)
        )
        report = self.report()
        self.assertEqual(
            report["pools"]["bulk"]["leases"], {"active": 1, "expired": 1, "missing_expiry": 0}
        )
        self.assertEqual(report["pools"]["bulk"]["artifacts"]["accepted_attempts"], 1)
        self.assertEqual(report["pools"]["bulk"]["artifacts"]["face_artifacts"], 1)
        self.assertEqual(report["pools"]["selfie"]["leases"]["expired"], 1)
        expired_attempt.refresh_from_db()
        self.assertEqual(expired_attempt.status, "in_progress")
        rendered = json.dumps(report)
        for secret in (
            "private-photo",
            "private-token",
            "private-key",
            "private-object-key",
            "https://private-url",
        ):
            self.assertNotIn(secret, rendered)

    @override_settings(PHOTO_PROCESSING_ENABLED=False)
    def test_disabled_endpoint_is_separate_from_durable_claimable_backlog(self):
        self.bulk_job()
        self.selfie_job()
        report = self.report()
        self.assertFalse(report["endpoint_enabled"])
        self.assertEqual(report["pools"]["bulk"]["claimable"], 1)
        self.assertEqual(report["pools"]["selfie"]["claimable"], 1)
        self.assertEqual(report["pools"]["bulk"]["jobs"]["queued"], 1)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=False, PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_disabled_enrollment_does_not_hide_existing_claimable_jobs(self):
        self.bulk_job(identity=(2, "face_embedding", 3))
        report = self.report()
        self.assertEqual(report["pools"]["bulk"]["claimable"], 1)
        self.assertFalse(report["enrollment_flags"]["face_enabled"])

    def test_pool_configuration_rejects_unknown_duplicate_and_cross_pool_identities(self):
        for options in (
            {"bulk_identities": "1/selfie_query/2"},
            {"selfie_identities": "1/capture_metadata/2"},
            {"bulk_identities": "9/secret-processor/9"},
            {"bulk_identities": "1/capture_metadata/2,1/capture_metadata/2"},
            {"bulk_identities": ""},
        ):
            with self.subTest(options=options), self.assertRaises(CommandError):
                self.report(**options)

    def test_configured_subset_cannot_count_other_bulk_identities(self):
        self.bulk_job()
        self.bulk_job(identity=(2, "face_embedding", 3))
        report = self.report(bulk_identities="1/capture_metadata/2")
        self.assertEqual(report["pools"]["bulk"]["jobs"]["queued"], 1)
        self.assertEqual(report["unassigned_processing_jobs"], 1)
