import hashlib
import json
from datetime import date
from typing import cast

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from ingestion.storage import ObjectMissing
from picflow.models import Event, Photo

from processing.contracts import ClaimedJob
from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CAPTURE_METADATA_PROCESSOR_VERSION,
    capture_metadata_configuration,
    request_capture_metadata,
    request_generate_preview,
)
from processing.services.jobs import claim_job, complete_attempt, fail_attempt
from processing.services.previews import complete_preview_attempt
from processing.services.reports import close_run_report, report_upper_bound_bytes
from processing.storage import ExactPreviewStorage, PreviewObject


class ProcessingRunReportTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="reports-owner")
        self.event = Event.objects.create(
            name="Reports event",
            slug="reports-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )

    def private_photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"report-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def claim(self):
        return claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-report",
        )

    def preview_photo(self, suffix: str) -> Photo:
        photo = self.private_photo(f"preview-{suffix}")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        return photo

    def claim_preview(self):
        return claim_job(
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            worker_build="preview-report-worker",
        )

    def close_one_successful_run(self) -> EventProcessingRun:
        photo = self.private_photo("one")
        request_capture_metadata(photo)
        claimed = self.claim()
        complete_attempt(
            claimed.attempt.id,
            result={"capture_time": None, "warnings": ["capture_time_missing"]},
            total_duration_ms=20,
        )
        run = close_run_report(claimed.job.run_id)
        assert run is not None
        return run

    def test_report_does_not_close_a_sealed_run_until_every_member_is_terminal(self) -> None:
        first = self.private_photo("first")
        second = self.private_photo("second")
        request_capture_metadata(first)
        request_capture_metadata(second)
        first_claim = self.claim()

        self.assertIsNone(close_run_report(first_claim.job.run_id))

        run = EventProcessingRun.objects.get(pk=first_claim.job.run_id)
        self.assertEqual(run.status, EventProcessingRun.Status.SEALED)

    def test_report_uses_only_the_exact_sealed_cohort_not_later_photos(self) -> None:
        first = self.private_photo("sealed")
        request_capture_metadata(first)
        claimed = self.claim()
        later = self.private_photo("later")
        request_capture_metadata(later)
        complete_attempt(claimed.attempt.id, result={"capture_time": None}, total_duration_ms=12)

        run = close_run_report(claimed.job.run_id)
        assert run is not None

        self.assertEqual(run.report["cohort_size"], 1)
        self.assertEqual([row["photo_id"] for row in run.report["photos"]], [first.id])
        self.assertEqual(
            EventProcessingRun.objects.get(event=self.event, status="collecting").jobs.count(), 1
        )

    def test_report_has_agreed_counts_duration_summary_and_bounded_photo_rows(self) -> None:
        photos = [self.private_photo("one"), self.private_photo("two"), self.private_photo("three")]
        for photo in photos:
            request_capture_metadata(photo)
        run_id = None
        while True:
            claimed = self.claim()
            if claimed.empty:
                break
            run_id = claimed.job.run_id
            if claimed.job.photo_id == photos[0].id:
                complete_attempt(
                    claimed.attempt.id,
                    result={"capture_time": "2026-07-29T10:00:00Z", "warnings": []},
                    total_duration_ms=30,
                )
            elif claimed.job.photo_id == photos[1].id:
                complete_attempt(
                    claimed.attempt.id,
                    result={"capture_time": None, "warnings": ["capture_time_missing"]},
                    total_duration_ms=10,
                )
            else:
                fail_attempt(
                    claimed.attempt.id,
                    error_code="unsupported_input",
                    error_detail="a deliberately long private implementation detail",
                    retryable=False,
                )

        assert run_id is not None
        run = close_run_report(run_id)
        assert run is not None

        self.assertEqual(run.report["cohort_size"], 3)
        self.assertEqual(
            run.report["counts"],
            {"denominator": 3, "succeeded": 2, "failed": 1, "cancelled": 0},
        )
        self.assertEqual(
            run.report["capture_time"],
            {"denominator": 2, "with_capture_time": 1, "without_capture_time": 1},
        )
        self.assertEqual(
            run.report["durations_ms"], {"denominator": 2, "min": 10, "median": 20, "max": 30}
        )
        self.assertEqual(len(run.report["photos"]), 3)
        for row in run.report["photos"]:
            self.assertLessEqual(
                set(row),
                {
                    "photo_id",
                    "status",
                    "accepted_attempt_id",
                    "capture_time_present",
                    "attempt_count",
                    "faces_detected",
                    "faces_embedded",
                    "faces_kept",
                    "faces_quality_rejected",
                    "faces_technical_failed",
                    "face_rejection_reasons",
                    "face_technical_failure_reasons",
                    "duration_ms",
                    "warnings",
                    "error_code",
                },
            )
            self.assertNotIn("error_detail", row)
            self.assertNotIn("signed_url", row)

    def test_closed_report_is_immutable_at_the_database_boundary(self) -> None:
        run = self.close_one_successful_run()

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                EventProcessingRun.objects.filter(pk=run.pk).update(report={"changed": True})

        run.refresh_from_db()
        self.assertEqual(run.status, EventProcessingRun.Status.CLOSED)
        self.assertEqual(run.report["cohort_size"], 1)

    def test_final_terminal_transition_automatically_closes_the_run(self) -> None:
        photo = self.private_photo("automatic-close")
        request_capture_metadata(photo)
        claimed = self.claim()

        complete_attempt(claimed.attempt.id, result={"capture_time": None}, total_duration_ms=5)

        run = EventProcessingRun.objects.get(pk=claimed.job.run_id)
        self.assertEqual(run.status, EventProcessingRun.Status.CLOSED)

    def test_report_preserves_half_millisecond_median(self) -> None:
        first = self.private_photo("median-one")
        second = self.private_photo("median-two")
        request_capture_metadata(first)
        request_capture_metadata(second)
        first_claim = self.claim()
        complete_attempt(
            first_claim.attempt.id, result={"capture_time": None}, total_duration_ms=10
        )
        second_claim = self.claim()
        complete_attempt(
            second_claim.attempt.id, result={"capture_time": None}, total_duration_ms=11
        )

        run = EventProcessingRun.objects.get(pk=first_claim.job.run_id)

        self.assertEqual(run.report["durations_ms"]["median"], 10.5)

    def test_report_exposes_face_embedding_counters_per_photo(self) -> None:
        photo = self.private_photo("faces")
        request_capture_metadata(photo)
        claimed = self.claim()
        now = timezone.now()
        ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=now,
            accepted=True,
            result={"capture_time": None},
        )
        claimed.job.status = ProcessingJob.Status.SUCCEEDED
        claimed.job.completed_at = now
        claimed.job.save(update_fields=["status", "completed_at"])
        EventProcessingRun.objects.filter(pk=claimed.job.run_id).update(
            status=EventProcessingRun.Status.SEALED,
            sealed_at=now,
        )

        artifact = FaceProcessingAttemptArtifact.objects.create(
            attempt_id=claimed.attempt.id,
            status=FaceProcessingAttemptArtifact.Status.COMPLETE,
            feature_payload={"detector": "unit"},
            quality_payload={"accepted": True},
        )
        kept = PhotoFaceDetection.objects.create(
            attempt_id=claimed.attempt.id,
            artifact=artifact,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
            geometry={"x": 0, "y": 0, "w": 1, "h": 1},
            features={"score": 0.9},
        )
        PhotoFaceDetection.objects.create(
            attempt_id=claimed.attempt.id,
            artifact=artifact,
            face_index=1,
            status=PhotoFaceDetection.Status.QUALITY_REJECTED,
            geometry={"x": 0, "y": 0, "w": 1, "h": 1},
            features={
                "quality": {
                    "decision": "quality_rejected",
                    "reasons": ["severe_blur"],
                }
            },
        )
        PhotoFaceDetection.objects.create(
            attempt_id=claimed.attempt.id,
            artifact=artifact,
            face_index=2,
            status=PhotoFaceDetection.Status.FAILED,
            geometry={"x": 2, "y": 2, "w": 1, "h": 1},
            features={"error_code": "model_inference_error"},
        )
        FaceEmbedding.objects.create(
            detection=kept,
            model_version="sface-v1",
            vector=[0.1, 0.2, 0.3],
            metadata={"source": "unit"},
        )

        run = close_run_report(claimed.job.run_id)
        assert run is not None

        row = run.report["photos"][0]
        self.assertEqual(row["faces_detected"], 3)
        self.assertEqual(row["faces_kept"], 1)
        self.assertEqual(row["faces_quality_rejected"], 1)
        self.assertEqual(row["faces_embedded"], 1)
        self.assertEqual(row["faces_technical_failed"], 1)
        self.assertEqual(row["face_rejection_reasons"], {"severe_blur": 1})
        self.assertEqual(row["face_technical_failure_reasons"], {"model_inference_error": 1})
        self.assertEqual(
            run.report["faces"],
            {
                "denominator": 1,
                "detected": 3,
                "kept": 1,
                "quality_rejected": 1,
                "embedded": 1,
                "technical_failed": 1,
                "rejection_reasons": {"severe_blur": 1},
                "technical_failure_reasons": {"model_inference_error": 1},
            },
        )

    def test_report_counts_retry_when_another_member_is_cancelled_without_attempt(self) -> None:
        retried = self.private_photo("retried")
        cancelled = self.private_photo("cancelled")
        request_capture_metadata(retried)
        request_capture_metadata(cancelled)
        first_claim = self.claim()
        fail_attempt(first_claim.attempt.id, error_code="network_interruption", retryable=True)
        other_job = ProcessingJob.objects.exclude(pk=first_claim.job.pk).get()
        other_job.status = ProcessingJob.Status.CANCELLED
        other_job.completed_at = timezone.now()
        other_job.save(update_fields=["status", "completed_at"])
        other_state = other_job.photo.processing_states.get(processor_type="capture_metadata")
        other_state.status = "cancelled"
        other_state.cancelled_at = timezone.now()
        other_state.save(update_fields=["status", "cancelled_at", "updated_at"])
        first_claim.job.refresh_from_db()
        retry = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="worker-report",
            now=first_claim.job.available_at,
        )
        assert isinstance(retry, ClaimedJob)
        complete_attempt(retry.attempt.id, result={"capture_time": None}, total_duration_ms=10)

        run = EventProcessingRun.objects.get(pk=first_claim.job.run_id)

        self.assertEqual(run.report["attempts"]["retries"], 1)

    def test_preview_report_exposes_only_bounded_output_evidence_and_stable_failures(self) -> None:
        """Catch a preview report that loses evidence or serializes media secrets."""
        accepted_photo = self.preview_photo("a-accepted")
        failed_photo = self.preview_photo("b-failed")
        cancelled_photo = self.preview_photo("c-cancelled")
        for photo in (accepted_photo, failed_photo, cancelled_photo):
            request_generate_preview(photo, pixel_width=3200, pixel_height=2000)

        first = self.claim_preview()
        self.assertEqual(first.job.photo_id, accepted_photo.id)
        content = b"preview-report-output"
        object = PreviewObject(
            etag_wire='"report-preview"',
            etag_value="report-preview",
            byte_size=len(content),
            content_type="image/jpeg",
            sha256=hashlib.sha256(content).hexdigest(),
            width=1600,
            height=1000,
        )

        class ReportPreviewStorage:
            final: PreviewObject | None = None

            def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
                if key.startswith("derivatives/"):
                    if self.final is None:
                        raise ObjectMissing()
                    return self.final
                return object

            def promote(
                self, *, staging_key: str, final_key: str, source_etag: str
            ) -> PreviewObject:
                self.final = object
                return object

        complete_preview_attempt(
            first.attempt.id,
            result={
                "variant": "preview-small-v1",
                "content_type": "image/jpeg",
                "byte_size": object.byte_size,
                "width": 1600,
                "height": 1000,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
                "sha256": object.sha256,
                "upload_ms": 17,
                "warnings": ["color_profile_missing"],
            },
            storage=cast(ExactPreviewStorage, ReportPreviewStorage()),
            download_duration_ms=7,
            compute_duration_ms=11,
            total_duration_ms=29,
        )
        ProcessingAttempt.objects.create(
            event=self.event,
            run=first.job.run,
            job=first.job,
            photo=accepted_photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=first.job.configuration,
            input_fingerprint=first.job.input_fingerprint,
            worker_build="stale-preview-worker",
            status=ProcessingAttempt.Status.STALE,
            terminal_at=timezone.now(),
            result={"signed_url": "https://must-not-escape.test/stale"},
            result_hash="b" * 64,
        )
        second = self.claim_preview()
        self.assertEqual(second.job.photo_id, failed_photo.id)
        fail_attempt(
            second.attempt.id,
            error_code="output_contract_violation",
            error_detail="processing-staging/previews/private-secret.jpg",
            retryable=False,
        )
        cancelled = cancelled_photo.processing_jobs.get(processor_type="generate_preview")
        cancelled.status = ProcessingJob.Status.CANCELLED
        cancelled.completed_at = timezone.now()
        cancelled.save(update_fields=["status", "completed_at"])
        cancelled_state = cancelled_photo.processing_states.get(processor_type="generate_preview")
        cancelled_state.status = PhotoProcessingState.Status.CANCELLED
        cancelled_state.cancelled_at = timezone.now()
        cancelled_state.save(update_fields=["status", "cancelled_at", "updated_at"])

        run = close_run_report(first.job.run_id)

        assert run is not None
        self.assertEqual(
            run.report["counts"],
            {"denominator": 3, "succeeded": 1, "failed": 1, "cancelled": 1},
        )
        self.assertEqual(run.report["attempts"], {"total": 3, "retries": 1, "stale": 1})
        self.assertEqual(
            run.report["preview"],
            {
                "accepted_outputs": 1,
                "output_bytes": {
                    "denominator": 1,
                    "min": object.byte_size,
                    "median": object.byte_size,
                    "max": object.byte_size,
                },
                "output_width": {"denominator": 1, "min": 1600, "median": 1600, "max": 1600},
                "output_height": {"denominator": 1, "min": 1000, "median": 1000, "max": 1000},
                "upload_durations_ms": {"denominator": 1, "min": 17, "median": 17, "max": 17},
                "download_durations_ms": {"denominator": 1, "min": 7, "median": 7, "max": 7},
                "compute_durations_ms": {"denominator": 1, "min": 11, "median": 11, "max": 11},
                "warnings": {"color_profile_missing": 1},
                "failure_codes": {"output_contract_violation": 1},
            },
        )
        accepted_row = next(
            row for row in run.report["photos"] if row["photo_id"] == accepted_photo.id
        )
        self.assertEqual(
            accepted_row["preview"],
            {
                "byte_size": object.byte_size,
                "width": 1600,
                "height": 1000,
                "download_ms": 7,
                "compute_ms": 11,
                "upload_ms": 17,
            },
        )
        serialized = json.dumps(run.report, sort_keys=True)
        for forbidden in (
            "originals/",
            "derivatives/",
            "processing-staging/",
            "must-not-escape",
            "https://",
            "image_bytes",
            '"exif"',
            object.sha256,
        ):
            self.assertNotIn(forbidden, serialized)

    def test_watermark_run_reuses_the_bounded_preview_report_projection(self) -> None:
        photo = self.private_photo("watermark-report")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        request_generate_preview(photo, pixel_width=3200, pixel_height=2000)
        clean_claim = self.claim_preview()
        clean_bytes = b"clean-report-output"
        clean_object = PreviewObject(
            etag_wire='"clean-report"',
            etag_value="clean-report",
            byte_size=len(clean_bytes),
            content_type="image/jpeg",
            sha256=hashlib.sha256(clean_bytes).hexdigest(),
            width=1600,
            height=1000,
        )

        class PublicationStorage:
            def __init__(self, object: PreviewObject) -> None:
                self.object = object
                self.final: PreviewObject | None = None

            def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
                if key.startswith("derivatives/"):
                    if self.final is None:
                        raise ObjectMissing()
                    return self.final
                return self.object

            def promote(self, **_: object) -> PreviewObject:
                self.final = self.object
                return self.object

        complete_preview_attempt(
            clean_claim.attempt.id,
            result={
                "variant": "preview-small-v1",
                "content_type": "image/jpeg",
                "byte_size": clean_object.byte_size,
                "width": clean_object.width,
                "height": clean_object.height,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
                "sha256": clean_object.sha256,
                "upload_ms": 13,
                "warnings": [],
            },
            storage=cast(ExactPreviewStorage, PublicationStorage(clean_object)),
        )
        watermark_claim = claim_job(
            contract_version=2,
            processor_type="generate_watermarked_preview",
            processor_version=1,
            worker_build="watermark-report-worker",
        )
        assert isinstance(watermark_claim, ClaimedJob)
        watermark_bytes = b"watermarked-report-output"
        watermark_object = PreviewObject(
            etag_wire='"watermark-report"',
            etag_value="watermark-report",
            byte_size=len(watermark_bytes),
            content_type="image/jpeg",
            sha256=hashlib.sha256(watermark_bytes).hexdigest(),
            width=1600,
            height=1000,
        )
        complete_preview_attempt(
            watermark_claim.attempt.id,
            result={
                "variant": "preview-watermarked-v1",
                "content_type": "image/jpeg",
                "byte_size": watermark_object.byte_size,
                "width": watermark_object.width,
                "height": watermark_object.height,
                "sha256": watermark_object.sha256,
                "upload_ms": 19,
                "warnings": [],
            },
            storage=cast(ExactPreviewStorage, PublicationStorage(watermark_object)),
            download_duration_ms=5,
            compute_duration_ms=7,
        )

        run = EventProcessingRun.objects.get(pk=watermark_claim.job.run_id)
        self.assertEqual(
            run.report["preview"]["accepted_outputs"],
            1,
        )
        self.assertEqual(run.report["photos"][0]["preview"]["upload_ms"], 19)
        serialized = json.dumps(run.report, sort_keys=True)
        self.assertNotIn("derivatives/", serialized)
        self.assertNotIn(watermark_object.sha256, serialized)

    def test_capped_cohort_report_preserves_every_row_within_recorded_byte_limit(self) -> None:
        configuration = capture_metadata_configuration(self.event.timezone_name)
        configured_limit = configuration["max_cohort_size"]
        assert isinstance(configured_limit, int)
        for number in range(configured_limit):
            request_capture_metadata(self.private_photo(f"bounded-{number}"))
        while True:
            claimed = self.claim()
            if claimed.empty:
                break
            complete_attempt(
                claimed.attempt.id,
                result={"capture_time": None, "warnings": ["capture_time_missing"]},
                total_duration_ms=1,
            )

        run = EventProcessingRun.objects.get(event=self.event)
        serialized = json.dumps(run.report, ensure_ascii=False, separators=(",", ":")).encode()
        configured_bytes = configuration["report_max_bytes"]
        assert isinstance(configured_bytes, int)
        self.assertEqual(run.report["cohort_size"], configured_limit)
        self.assertEqual(len(run.report["photos"]), configured_limit)
        self.assertLessEqual(len(serialized), configured_bytes)

    def test_worst_case_configured_cohort_always_fits_the_report_limit(self) -> None:
        configuration = capture_metadata_configuration(self.event.timezone_name)
        configured_limit = configuration["max_cohort_size"]
        assert isinstance(configured_limit, int)
        for number in range(configured_limit):
            request_capture_metadata(self.private_photo(f"worst-{number}"))
        run = EventProcessingRun.objects.get(event=self.event)
        now = timezone.now()
        control_character = "\x1f"
        for job_number, job in enumerate(run.jobs.order_by("photo_id")):
            for attempt_number, status in enumerate(
                [
                    ProcessingAttempt.Status.FAILED,
                    ProcessingAttempt.Status.SUCCEEDED,
                    ProcessingAttempt.Status.STALE,
                ]
            ):
                accepted = status == ProcessingAttempt.Status.SUCCEEDED
                ProcessingAttempt.objects.create(
                    event=self.event,
                    run=run,
                    job=job,
                    photo=job.photo,
                    contract_version=job.contract_version,
                    processor_type=job.processor_type,
                    processor_version=job.processor_version,
                    configuration=job.configuration,
                    input_fingerprint=job.input_fingerprint,
                    worker_build=(f"{job_number:03d}-{attempt_number}" + control_character * 128)[
                        :128
                    ],
                    status=status,
                    terminal_at=now,
                    result={
                        "capture_time": None,
                        "warnings": [control_character * 32] * 8,
                        "retryable": True,
                    },
                    result_hash=f"{job_number:02d}{attempt_number}".ljust(64, "h"),
                    error_code=control_character * 64,
                    error_detail=control_character * 512,
                    total_duration_ms=1,
                    accepted=accepted,
                )
            job.status = ProcessingJob.Status.SUCCEEDED
            job.completed_at = now
            job.save(update_fields=["status", "completed_at"])
        run.status = EventProcessingRun.Status.SEALED
        run.sealed_at = now
        run.save(update_fields=["status", "sealed_at"])

        closed = close_run_report(run.id)

        assert closed is not None
        configured_bytes = configuration["report_max_bytes"]
        assert isinstance(configured_bytes, int)
        serialized = json.dumps(closed.report, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertEqual(len(closed.report["photos"]), configured_limit)
        self.assertLessEqual(report_upper_bound_bytes(closed.configuration), configured_bytes)
        self.assertLessEqual(len(serialized), configured_bytes)
