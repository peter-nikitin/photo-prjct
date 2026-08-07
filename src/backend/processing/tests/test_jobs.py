# mypy: disable-error-code=union-attr

from datetime import date, timedelta
from queue import Queue
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo

from processing.contracts import ClaimedJob, CompletionConflict
from processing.models import (
    EventProcessingRun,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services.enrollment import (
    CAPTURE_METADATA_PROCESSOR_VERSION,
    GENERATE_PREVIEW_CONFIGURATION,
    capture_metadata_configuration,
    request_capture_metadata,
    request_face_embedding_enqueue,
    request_processor,
)
from processing.services.jobs import (
    MAX_ATTEMPTS,
    claim_job,
    complete_attempt,
    fail_attempt,
    heartbeat_attempt,
    recover_expired_attempts,
    refresh_download,
)


class ProcessingJobServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="jobs-owner")
        self.event = Event.objects.create(
            name="Jobs event",
            slug="jobs-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )

    def private_photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"job-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def test_claim_seals_the_enrolled_cohort_and_creates_a_leased_current_attempt(self) -> None:
        first = self.private_photo("first")
        second = self.private_photo("second")
        request_capture_metadata(first)
        request_capture_metadata(second)

        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            lease_seconds=120,
        )

        self.assertFalse(claimed.empty)
        self.assertEqual(claimed.job.photo_id, first.id)
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)
        self.assertGreater(claimed.attempt.lease_expires_at, timezone.now())
        run = EventProcessingRun.objects.get(pk=claimed.job.run_id)
        self.assertEqual(run.status, EventProcessingRun.Status.SEALED)
        self.assertEqual(set(run.jobs.values_list("photo_id", flat=True)), {first.id, second.id})
        state = PhotoProcessingState.objects.get(photo=first, processor_type="capture_metadata")
        self.assertEqual(state.status, PhotoProcessingState.Status.PROCESSING)
        self.assertEqual(state.current_attempt_id, claimed.attempt.id)

    def test_claim_only_selects_an_exactly_compatible_processor(self) -> None:
        photo = self.private_photo("compatible")
        request_capture_metadata(photo)

        empty = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            worker_build="worker-1",
        )

        self.assertTrue(empty.empty)
        self.assertEqual(ProcessingJob.objects.get().status, ProcessingJob.Status.QUEUED)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_face_claim_only_selects_an_exactly_compatible_processor(self) -> None:
        photo = self.private_photo("face-compatible")
        request_face_embedding_enqueue(photo)

        claim = claim_job(
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            worker_build="worker-1",
        )
        mismatch_version = claim_job(
            contract_version=1,
            processor_type="face_embedding",
            processor_version=2,
            worker_build="worker-1",
        )

        self.assertFalse(claim.empty)
        self.assertEqual(claim.job.processor_type, "face_embedding")
        self.assertEqual(claim.job.processor_version, 1)
        self.assertTrue(mismatch_version.empty)

    def test_empty_queue_returns_an_explicit_backoff_response(self) -> None:
        empty = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )

        self.assertTrue(empty.empty)
        self.assertGreaterEqual(empty.suggested_delay_seconds, 1)

    def test_unverified_preview_result_cannot_bypass_the_publication_service(self) -> None:
        photo = self.private_photo("preview-publication-boundary")
        request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )
        claimed = claim_job(
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            worker_build="preview-worker",
        )

        with self.assertRaises(ValueError):
            complete_attempt(
                claimed.attempt.id,
                result={"variant": "preview-small-v1"},
            )

        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_preview_face_detection_persists_derivative_coordinate_space_and_scale(self) -> None:
        photo = self.private_photo("preview-face-geometry")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        preview_state = request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )
        assert preview_state.current_job is not None
        preview_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=preview_state.current_job.run,
            job=preview_state.current_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint=preview_state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=(
                f"derivatives/previews/{photo.id}/preview-small-v1/"
                f"{preview_attempt.id}-{'a' * 64}.jpg"
            ),
            byte_size=1024,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=3200,
            oriented_source_height=2000,
            sha256="a" * 64,
            accepted_attempt=preview_attempt,
        )
        preview_state.status = PhotoProcessingState.Status.SUCCEEDED
        preview_state.accepted_attempt = preview_attempt
        preview_state.succeeded_at = timezone.now()
        preview_state.save(
            update_fields=["status", "accepted_attempt", "succeeded_at", "updated_at"]
        )

        face_state = request_face_embedding_enqueue(photo)
        assert face_state.current_job is not None
        claimed = claim_job(
            contract_version=2,
            processor_type="face_embedding",
            processor_version=2,
            worker_build="worker-2",
        )
        complete_attempt(
            claimed.attempt.id,
            result={
                "model": "sface",
                "face_count": 1,
                "faces": [
                    {
                        "index": 0,
                        "bbox": [10, 20, 30, 40],
                        "confidence": 0.9,
                        "landmarks": [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]],
                        "embedding": [0.1, 0.2],
                    }
                ],
                "warnings": [],
                "input_geometry": {
                    "coordinate_space": "preview-small-v1",
                    "pixel_width": derivative.width,
                    "pixel_height": derivative.height,
                    "oriented_source_width": derivative.oriented_source_width,
                    "oriented_source_height": derivative.oriented_source_height,
                },
            },
        )

        detection = PhotoFaceDetection.objects.get(attempt=claimed.attempt)
        self.assertEqual(
            detection.geometry,
            {
                "bbox": [10.0, 20.0, 30.0, 40.0],
                "landmarks": [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]],
                "model": "sface",
                "coordinate_space": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
                "scale_x": 2.0,
                "scale_y": 2.0,
            },
        )

    def test_later_eligible_photo_enters_a_new_collecting_run_after_first_claim(self) -> None:
        first = self.private_photo("sealed")
        request_capture_metadata(first)
        first_claim = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )
        later = self.private_photo("later")

        request_capture_metadata(later)

        self.assertEqual(EventProcessingRun.objects.count(), 2)
        sealed_run = EventProcessingRun.objects.get(pk=first_claim.job.run_id)
        later_job = ProcessingJob.objects.get(photo=later)
        self.assertEqual(sealed_run.status, EventProcessingRun.Status.SEALED)
        self.assertEqual(list(sealed_run.jobs.values_list("photo_id", flat=True)), [first.id])
        self.assertEqual(later_job.run.status, EventProcessingRun.Status.COLLECTING)

    def test_database_rejects_new_job_membership_after_a_run_is_sealed(self) -> None:
        first = self.private_photo("sealed-membership")
        request_capture_metadata(first)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )
        later = self.private_photo("forbidden-membership")

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingJob.objects.create(
                    event=self.event,
                    run=claimed.job.run,
                    photo=later,
                    contract_version=claimed.job.contract_version,
                    processor_type=claimed.job.processor_type,
                    processor_version=claimed.job.processor_version,
                    configuration=claimed.job.configuration,
                    configuration_hash=claimed.job.configuration_hash,
                    input_fingerprint=claimed.job.input_fingerprint,
                )

    def test_heartbeat_renews_only_the_current_unexpired_lease(self) -> None:
        photo = self.private_photo("heartbeat")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
            lease_seconds=120,
        )

        renewed = heartbeat_attempt(
            claimed.attempt.id, now=now + timedelta(seconds=10), lease_seconds=120
        )
        not_renewed = heartbeat_attempt(claimed.attempt.id, now=now + timedelta(seconds=131))

        self.assertEqual(renewed.lease_expires_at, now + timedelta(seconds=130))
        self.assertIsNone(not_renewed)

    def test_claim_and_heartbeat_require_the_immutable_run_lease_duration(self) -> None:
        photo = self.private_photo("fixed-lease")
        request_capture_metadata(photo)
        configured = capture_metadata_configuration(self.event.timezone_name)["worker"]
        assert isinstance(configured, dict)
        lease_seconds = configured["lease_duration_seconds"]
        assert isinstance(lease_seconds, int)
        now = timezone.now()

        with self.assertRaises(ValueError):
            claim_job(
                contract_version=1,
                processor_type="capture_metadata",
                processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
                worker_build="worker-1",
                lease_seconds=lease_seconds - 1,
                now=now,
            )

        self.assertEqual(ProcessingAttempt.objects.count(), 0)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            lease_seconds=lease_seconds,
            now=now,
        )
        original_expiry = claimed.attempt.lease_expires_at

        with self.assertRaises(ValueError):
            heartbeat_attempt(
                claimed.attempt.id,
                lease_seconds=lease_seconds - 1,
                now=now + timedelta(seconds=1),
            )

        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.lease_expires_at, original_expiry)

    def test_download_refresh_requires_current_unexpired_lease(self) -> None:
        photo = self.private_photo("refresh")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
            lease_seconds=120,
        )

        self.assertEqual(
            refresh_download(claimed.attempt.id, now=now + timedelta(seconds=119)).id,
            claimed.attempt.id,
        )
        self.assertIsNone(refresh_download(claimed.attempt.id, now=now + timedelta(seconds=120)))

    def test_retryable_failure_waits_with_bounded_backoff_then_permanent_failure_ends_job(
        self,
    ) -> None:
        photo = self.private_photo("retries")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
        )

        failed = fail_attempt(
            claimed.attempt.id,
            error_code="network_interruption",
            retryable=True,
            now=now + timedelta(seconds=1),
        )
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        job = ProcessingJob.objects.get(pk=claimed.job.id)

        self.assertEqual(failed.attempt.status, ProcessingAttempt.Status.FAILED)
        self.assertEqual(state.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertGreater(job.available_at, now)

        retry = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=job.available_at,
        )
        fail_attempt(
            retry.attempt.id,
            error_code="network_interruption",
            retryable=True,
            now=job.available_at + timedelta(seconds=1),
        )
        job.refresh_from_db()
        final_retry = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=job.available_at,
        )
        fail_attempt(
            final_retry.attempt.id,
            error_code="network_interruption",
            retryable=True,
            now=job.available_at + timedelta(seconds=1),
        )
        job.refresh_from_db()
        state.refresh_from_db()

        self.assertEqual(ProcessingAttempt.objects.filter(job=job).count(), MAX_ATTEMPTS)
        self.assertEqual(job.status, ProcessingJob.Status.FAILED)
        self.assertEqual(state.status, PhotoProcessingState.Status.FAILED)

    def test_permanent_failure_does_not_retry(self) -> None:
        photo = self.private_photo("permanent")
        request_capture_metadata(photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )

        fail_attempt(claimed.attempt.id, error_code="unsupported_input", retryable=False)

        job = ProcessingJob.objects.get(pk=claimed.job.id)
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        self.assertEqual(job.status, ProcessingJob.Status.FAILED)
        self.assertEqual(state.status, PhotoProcessingState.Status.FAILED)

    def test_fingerprint_mismatch_retries_with_the_same_immutable_input_fingerprint(self) -> None:
        photo = self.private_photo("fingerprint-retry")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
        )
        fingerprint = claimed.job.input_fingerprint

        fail_attempt(
            claimed.attempt.id,
            error_code="fingerprint_mismatch",
            retryable=True,
            now=now + timedelta(seconds=1),
            jitter=lambda _low, _high: 0,
        )

        job = ProcessingJob.objects.get(pk=claimed.job.id)
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        retry = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-2",
            now=job.available_at,
        )

        self.assertEqual(job.status, ProcessingJob.Status.RETRY_WAIT)
        self.assertEqual(state.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertEqual(retry.job.id, job.id)
        self.assertEqual(retry.job.input_fingerprint, fingerprint)
        self.assertEqual(retry.attempt.input_fingerprint, fingerprint)

    def test_recovery_closes_expired_lease_and_schedules_its_retry(self) -> None:
        photo = self.private_photo("expired")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
            lease_seconds=120,
        )

        recovered = recover_expired_attempts(now=now + timedelta(seconds=120))

        self.assertEqual([attempt.id for attempt in recovered], [claimed.attempt.id])
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        job = ProcessingJob.objects.get(pk=claimed.job.id)
        self.assertEqual(recovered[0].status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(state.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertIsNone(state.current_attempt)
        self.assertEqual(job.status, ProcessingJob.Status.RETRY_WAIT)

    def test_recovery_respects_its_explicit_batch_limit(self) -> None:
        first = self.private_photo("recovery-limit-first")
        second = self.private_photo("recovery-limit-second")
        request_capture_metadata(first)
        request_capture_metadata(second)
        now = timezone.now()
        first_claim = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
        )
        second_claim = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
        )
        ProcessingAttempt.objects.filter(
            pk__in=[first_claim.attempt.id, second_claim.attempt.id]
        ).update(lease_expires_at=now - timedelta(seconds=1))

        recovered = recover_expired_attempts(now=now, limit=1)

        self.assertEqual(len(recovered), 1)
        self.assertEqual(
            ProcessingAttempt.objects.filter(status=ProcessingAttempt.Status.EXPIRED).count(),
            1,
        )
        self.assertEqual(
            ProcessingAttempt.objects.filter(status=ProcessingAttempt.Status.IN_PROGRESS).count(),
            1,
        )

    def test_expired_success_submission_recovers_state_and_records_late_receipt(self) -> None:
        photo = self.private_photo("expiry-submission")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
            lease_seconds=120,
        )

        completion = complete_attempt(
            claimed.attempt.id,
            result={"capture_time": None, "warnings": ["capture_time_missing"]},
            now=now + timedelta(seconds=120),
        )

        claimed.attempt.refresh_from_db()
        job = ProcessingJob.objects.get(pk=claimed.job.id)
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        receipt = ProcessingLateReceipt.objects.get(attempt=claimed.attempt)
        self.assertTrue(completion.stale)
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(claimed.attempt.error_code, "lease_expired")
        self.assertEqual(job.status, ProcessingJob.Status.RETRY_WAIT)
        self.assertEqual(state.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertEqual(receipt.payload["outcome"], "success")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingLateReceipt.objects.filter(pk=receipt.pk).update(
                    payload={"changed": True}
                )

    def test_late_receipt_after_recovery_is_idempotent_and_rejects_conflict(self) -> None:
        photo = self.private_photo("late-after-recovery")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
            lease_seconds=120,
        )
        recover_expired_attempts(now=now + timedelta(seconds=120))

        first = complete_attempt(
            claimed.attempt.id,
            result={"capture_time": None},
            now=now + timedelta(seconds=121),
        )
        repeated = complete_attempt(
            claimed.attempt.id,
            result={"capture_time": None},
            now=now + timedelta(seconds=122),
        )

        self.assertTrue(first.stale)
        self.assertTrue(repeated.idempotent)
        self.assertEqual(ProcessingLateReceipt.objects.filter(attempt=claimed.attempt).count(), 1)
        with self.assertRaises(CompletionConflict):
            complete_attempt(
                claimed.attempt.id,
                result={"capture_time": "2026-07-29T10:00:00Z"},
                now=now + timedelta(seconds=123),
            )

    def test_retryability_is_part_of_immutable_failure_identity(self) -> None:
        photo = self.private_photo("failure-identity")
        request_capture_metadata(photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )

        fail_attempt(
            claimed.attempt.id,
            error_code="network_interruption",
            retryable=True,
        )

        with self.assertRaises(CompletionConflict):
            fail_attempt(
                claimed.attempt.id,
                error_code="network_interruption",
                retryable=False,
            )

    def test_retry_delay_uses_recorded_policy_and_injectable_bounded_jitter(self) -> None:
        photo = self.private_photo("jitter")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
        )

        fail_attempt(
            claimed.attempt.id,
            error_code="network_interruption",
            retryable=True,
            now=now,
            jitter=lambda _low, _high: 4,
        )

        claimed.job.refresh_from_db()
        policy = capture_metadata_configuration(self.event.timezone_name)["retry_policy"]
        assert isinstance(policy, dict)
        max_attempts = policy["max_attempts"]
        assert isinstance(max_attempts, int)
        self.assertEqual(claimed.job.available_at, now + timedelta(seconds=34))
        self.assertEqual(max_attempts, MAX_ATTEMPTS)

    def test_full_collecting_run_overflows_without_sealing_until_its_first_claim(self) -> None:
        configured_cohort_limit = capture_metadata_configuration(self.event.timezone_name)[
            "max_cohort_size"
        ]
        assert isinstance(configured_cohort_limit, int)
        cohort_limit = configured_cohort_limit
        for number in range(cohort_limit + 1):
            request_capture_metadata(self.private_photo(f"overflow-{number}"))

        runs = list(EventProcessingRun.objects.order_by("created_at"))

        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0].status, EventProcessingRun.Status.COLLECTING)
        self.assertEqual(runs[0].jobs.count(), cohort_limit)
        self.assertEqual(runs[1].status, EventProcessingRun.Status.COLLECTING)
        self.assertEqual(runs[1].jobs.count(), 1)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )
        runs[0].refresh_from_db()
        runs[1].refresh_from_db()
        self.assertEqual(claimed.job.run_id, runs[0].id)
        self.assertEqual(runs[0].status, EventProcessingRun.Status.SEALED)
        self.assertEqual(runs[1].status, EventProcessingRun.Status.COLLECTING)


class ProcessingConcurrentCompletionTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="concurrent-owner")
        self.event = Event.objects.create(
            name="Concurrent event",
            slug="concurrent-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )

    def _photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"concurrent-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def test_simultaneous_completions_close_one_run_without_deadlock(self) -> None:
        first = self._photo("one")
        second = self._photo("two")
        request_capture_metadata(first)
        request_capture_metadata(second)
        first_claim = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )
        second_claim = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )
        barrier = Barrier(3)
        failures: Queue[BaseException] = Queue()

        def complete(attempt_id) -> None:
            close_old_connections()
            try:
                barrier.wait()
                complete_attempt(attempt_id, result={"capture_time": None}, total_duration_ms=1)
            except BaseException as error:  # noqa: BLE001
                failures.put(error)
            finally:
                close_old_connections()

        workers = [
            Thread(target=complete, args=(first_claim.attempt.id,)),
            Thread(target=complete, args=(second_claim.attempt.id,)),
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertTrue(failures.empty())
        self.assertEqual(
            EventProcessingRun.objects.get(pk=first_claim.job.run_id).status,
            EventProcessingRun.Status.CLOSED,
        )

    def test_simultaneous_claims_take_distinct_ready_jobs_instead_of_returning_empty(self) -> None:
        request_capture_metadata(self._photo("claim-one"))
        request_capture_metadata(self._photo("claim-two"))
        barrier = Barrier(3)
        results: Queue[object] = Queue()

        def claim() -> None:
            close_old_connections()
            try:
                barrier.wait()
                results.put(
                    claim_job(
                        contract_version=1,
                        processor_type="capture_metadata",
                        processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
                        worker_build="worker-claim",
                    )
                )
            finally:
                close_old_connections()

        workers = [Thread(target=claim), Thread(target=claim)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        claims = [results.get_nowait(), results.get_nowait()]
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertTrue(all(isinstance(claimed, ClaimedJob) for claimed in claims))
        claimed_jobs = [claimed for claimed in claims if isinstance(claimed, ClaimedJob)]
        self.assertEqual(
            {claimed.job.id for claimed in claimed_jobs},
            set(ProcessingJob.objects.values_list("id", flat=True)),
        )

    def test_identical_success_completion_is_idempotent_but_conflicting_duplicate_is_rejected(
        self,
    ) -> None:
        photo = self._photo("complete")
        request_capture_metadata(photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
        )

        first = complete_attempt(
            claimed.attempt.id,
            result={"capture_time": None, "warnings": ["capture_time_missing"]},
            total_duration_ms=12,
        )
        repeated = complete_attempt(
            claimed.attempt.id,
            result={"warnings": ["capture_time_missing"], "capture_time": None},
            total_duration_ms=12,
        )

        self.assertFalse(first.idempotent)
        self.assertTrue(repeated.idempotent)
        with self.assertRaises(CompletionConflict):
            complete_attempt(claimed.attempt.id, result={"capture_time": "2026-01-01T00:00:00Z"})

    def test_stale_completion_is_retained_without_changing_the_new_current_state(self) -> None:
        photo = self._photo("stale")
        request_capture_metadata(photo)
        now = timezone.now()
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-1",
            now=now,
        )
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        successor = ProcessingAttempt.objects.create(
            event=self.event,
            run=claimed.job.run,
            job=claimed.job,
            photo=photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            configuration=claimed.job.configuration,
            input_fingerprint=claimed.job.input_fingerprint,
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=60),
        )
        state.current_attempt = successor
        state.save(update_fields=["current_attempt", "updated_at"])

        stale = complete_attempt(
            claimed.attempt.id, result={"capture_time": None}, now=now + timedelta(seconds=1)
        )

        state.refresh_from_db()
        self.assertTrue(stale.stale)
        self.assertEqual(stale.attempt.status, ProcessingAttempt.Status.STALE)
        self.assertEqual(state.current_attempt_id, successor.id)
        repeated = complete_attempt(
            claimed.attempt.id, result={"capture_time": None}, now=now + timedelta(seconds=2)
        )
        self.assertTrue(repeated.idempotent)
        self.assertTrue(repeated.stale)
        self.assertFalse(ProcessingLateReceipt.objects.filter(attempt=claimed.attempt).exists())
        with self.assertRaises(CompletionConflict):
            complete_attempt(
                claimed.attempt.id,
                result={"capture_time": "2026-07-29T10:00:00Z"},
                now=now + timedelta(seconds=3),
            )
