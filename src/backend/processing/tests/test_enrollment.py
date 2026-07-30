from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    FACE_EMBEDDING_PROCESSOR,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CAPTURE_METADATA_CONFIGURATION,
    FACE_EMBEDDING_CONFIGURATION,
    reconcile_face_embedding,
    reconcile_capture_metadata,
    request_face_embedding_enqueue,
    request_capture_metadata,
)


class CaptureMetadataEnrollmentTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="enrollment-owner")
        self.event = Event.objects.create(
            name="Enrollment event",
            slug="enrollment-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def private_photo(self, suffix: str, *, content_type: str = "image/jpeg") -> Photo:
        return Photo.objects.create(
            id=f"private-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{suffix}",
            original_filename=f"{suffix}.jpg",
            original_size=10,
            original_content_type=content_type,
            uploaded_at=timezone.now(),
        )

    def test_new_photo_starts_not_requested(self) -> None:
        photo = self.private_photo("new")
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        self.assertEqual(state.status, PhotoProcessingState.Status.NOT_REQUESTED)
        self.assertIsNone(state.current_job)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=False)
    def test_face_embedding_request_is_gated_by_feature_flag(self) -> None:
        photo = self.private_photo("face-disabled")
        state = request_face_embedding_enqueue(photo)

        self.assertEqual(state.processor_type, FACE_EMBEDDING_PROCESSOR)
        self.assertEqual(state.status, PhotoProcessingState.Status.NOT_REQUESTED)
        self.assertIsNone(state.current_job)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_face_embedding_request_creates_job_with_expected_configuration(self) -> None:
        photo = self.private_photo("face-enabled")
        state = request_face_embedding_enqueue(photo)
        assert state.current_job is not None

        run = state.current_run
        self.assertEqual(run.processor_type, FACE_EMBEDDING_PROCESSOR)
        self.assertEqual(run.contract_version, 1)
        self.assertEqual(run.processor_version, 1)
        self.assertEqual(state.current_job.configuration["face_embedding"], FACE_EMBEDDING_CONFIGURATION["face_embedding"])
        self.assertEqual(state.current_job.processor_type, FACE_EMBEDDING_PROCESSOR)
        self.assertEqual(state.current_job.processor_version, 1)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_face_reconciliation_creates_missing_states_and_bounded_jobs(self) -> None:
        first = self.private_photo("face-first")
        second = self.private_photo("face-second")
        third = self.private_photo("face-third")
        legacy = Photo.objects.create(
            id="legacy",
            event=self.event,
            src="legacy.jpg",
        )

        reconciled = reconcile_face_embedding(limit=2)

        self.assertEqual([state.photo_id for state in reconciled], [first.pk, second.pk])
        self.assertEqual(ProcessingJob.objects.count(), 2)
        self.assertEqual(
            PhotoProcessingState.objects.get(photo=first, processor_type=FACE_EMBEDDING_PROCESSOR).status,
            PhotoProcessingState.Status.QUEUED,
        )
        self.assertEqual(
            PhotoProcessingState.objects.get(photo=second, processor_type=FACE_EMBEDDING_PROCESSOR).status,
            PhotoProcessingState.Status.QUEUED,
        )
        self.assertEqual(
            [state.photo_id for state in reconcile_face_embedding(limit=2)],
            [third.pk],
        )
        self.assertEqual(
            PhotoProcessingState.objects.get(
                photo=third, processor_type=FACE_EMBEDDING_PROCESSOR
            ).status,
            PhotoProcessingState.Status.QUEUED,
        )
        self.assertFalse(
            PhotoProcessingState.objects.filter(
                photo=legacy, processor_type=FACE_EMBEDDING_PROCESSOR
            ).exists()
        )

    def test_eligible_photos_share_compatible_collecting_run_and_queue_jobs(self) -> None:
        first = self.private_photo("first")
        second = self.private_photo("second")

        request_capture_metadata(first)
        request_capture_metadata(second)

        run = EventProcessingRun.objects.get()
        self.assertEqual(run.event, self.event)
        self.assertEqual(run.status, EventProcessingRun.Status.COLLECTING)
        self.assertEqual(run.contract_version, 1)
        self.assertEqual(run.processor_type, "capture_metadata")
        self.assertEqual(run.processor_version, 1)
        self.assertEqual(ProcessingJob.objects.filter(run=run).count(), 2)
        for photo in (first, second):
            state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
            self.assertEqual(state.status, PhotoProcessingState.Status.QUEUED)
            self.assertEqual(state.current_run, run)
            self.assertEqual(state.current_job.status, ProcessingJob.Status.QUEUED)

    def test_v1_configuration_records_worker_and_exif_behavior_immutably(self) -> None:
        self.assertEqual(
            CAPTURE_METADATA_CONFIGURATION["capture_metadata"],
            {
                "date_field_precedence": [
                    "DateTimeOriginal",
                    "DateTimeDigitized",
                    "DateTime",
                ],
                "normalization": "utc_assume_utc_if_missing",
            },
        )
        self.assertEqual(
            CAPTURE_METADATA_CONFIGURATION["worker"],
            {
                "concurrency": 1,
                "api_response_max_bytes": 16_384,
                "heartbeat_interval_seconds": 30,
                "lease_duration_seconds": 120,
                "max_input_bytes": 52_428_800,
                "max_pixels": 100_000_000,
                "poll_min_delay_seconds": 5,
                "terminal_result_max_bytes": 8_192,
            },
        )

    def test_empty_verified_etag_is_normalized_to_unavailable_evidence(self) -> None:
        state = request_capture_metadata(self.private_photo("empty-etag"), verified_source_etag="")

        assert state.current_job is not None
        self.assertEqual(
            state.current_job.input_fingerprint["verified_source_etag"],
            None,
        )
        self.assertEqual(state.current_job.input_fingerprint["version_evidence"], "unavailable")

    def test_enrollment_persists_explicit_unavailable_version_evidence(self) -> None:
        photo = self.private_photo("fingerprint")

        state = request_capture_metadata(photo)

        self.assertEqual(
            state.current_job.input_fingerprint,
            {
                "original_key": "originals/fingerprint",
                "original_size": 10,
                "original_content_type": "image/jpeg",
                "verified_source_etag": None,
                "version_evidence": "unavailable",
            },
        )

    def test_enrolled_job_fingerprint_can_be_copied_to_an_attempt(self) -> None:
        state = request_capture_metadata(self.private_photo("attempt"))
        job = state.current_job
        assert job is not None

        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=job.run,
            job=job,
            photo=job.photo,
            contract_version=job.contract_version,
            processor_type=job.processor_type,
            processor_version=job.processor_version,
            configuration=job.configuration,
            input_fingerprint=job.input_fingerprint,
        )

        self.assertEqual(attempt.input_fingerprint, job.input_fingerprint)

    def test_repeated_enrollment_is_a_no_op(self) -> None:
        photo = self.private_photo("repeat")
        request_capture_metadata(photo)
        request_capture_metadata(photo)

        self.assertEqual(EventProcessingRun.objects.count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 1)
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        self.assertEqual(state.status, PhotoProcessingState.Status.QUEUED)

    def test_legacy_and_ineligible_photos_remain_not_requested(self) -> None:
        legacy = Photo.objects.create(id="legacy", event=self.event, src="photos/legacy.jpg")
        png = self.private_photo("png", content_type="image/png")

        request_capture_metadata(legacy)
        request_capture_metadata(png)

        self.assertEqual(EventProcessingRun.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        for photo in (legacy, png):
            state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
            self.assertEqual(state.status, PhotoProcessingState.Status.NOT_REQUESTED)

    def test_reconciliation_queues_a_bounded_batch_of_eligible_unassigned_photos(self) -> None:
        first = self.private_photo("first")
        second = self.private_photo("second")
        third = self.private_photo("third")
        legacy = Photo.objects.create(id="legacy", event=self.event, src="photos/legacy.jpg")

        reconciled = reconcile_capture_metadata(limit=2)

        self.assertEqual([state.photo_id for state in reconciled], [first.pk, second.pk])
        self.assertEqual(ProcessingJob.objects.count(), 2)
        self.assertEqual(
            PhotoProcessingState.objects.get(
                photo=first, processor_type="capture_metadata"
            ).current_job.input_fingerprint,
            {
                "original_key": "originals/first",
                "original_size": 10,
                "original_content_type": "image/jpeg",
                "verified_source_etag": None,
                "version_evidence": "unavailable",
            },
        )
        self.assertEqual(
            PhotoProcessingState.objects.get(photo=third, processor_type="capture_metadata").status,
            PhotoProcessingState.Status.NOT_REQUESTED,
        )
        self.assertEqual(
            PhotoProcessingState.objects.get(
                photo=legacy, processor_type="capture_metadata"
            ).status,
            PhotoProcessingState.Status.NOT_REQUESTED,
        )
        self.assertEqual(
            [state.photo_id for state in reconcile_capture_metadata(limit=2)],
            [third.pk],
        )
        self.assertEqual(ProcessingJob.objects.count(), 3)
