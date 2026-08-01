from datetime import date
from typing import cast

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    FACE_EMBEDDING_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CAPTURE_METADATA_CONFIGURATION,
    FACE_EMBEDDING_CONFIGURATION,
    GENERATE_PREVIEW_CONFIGURATION,
    reconcile_capture_metadata,
    reconcile_face_embedding,
    request_capture_metadata,
    request_face_embedding_enqueue,
    request_processor,
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
        self.assertEqual(
            state.current_job.configuration["face_embedding"],
            FACE_EMBEDDING_CONFIGURATION["face_embedding"],
        )
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
            PhotoProcessingState.objects.get(
                photo=first, processor_type=FACE_EMBEDDING_PROCESSOR
            ).status,
            PhotoProcessingState.Status.QUEUED,
        )
        self.assertEqual(
            PhotoProcessingState.objects.get(
                photo=second, processor_type=FACE_EMBEDDING_PROCESSOR
            ).status,
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

    def test_v2_preview_configuration_records_all_output_affecting_rules(self) -> None:
        self.assertEqual(
            GENERATE_PREVIEW_CONFIGURATION["generate_preview"],
            {
                "variant": "preview-small-v1",
                "output_format": "jpeg",
                "max_long_edge": 1600,
                "jpeg_quality": 85,
                "color_space": "srgb",
                "upscale": False,
                "apply_exif_orientation": True,
                "strip_metadata": True,
                "watermark": "none",
                "max_output_bytes": 10_485_760,
                "max_output_width": 1600,
                "max_output_height": 1600,
                "checksum_algorithm": "sha256",
            },
        )
        worker_configuration = cast(dict[str, object], GENERATE_PREVIEW_CONFIGURATION["worker"])
        self.assertEqual(worker_configuration["max_pixels"], 24_000_000)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_preview_first_face_enrollment_uses_only_the_published_derivative(self) -> None:
        photo = self.private_photo("preview-face")
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
        preview_job = preview_state.current_job
        assert preview_job is not None
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=preview_job.run,
            job=preview_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint=preview_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=(
                f"derivatives/previews/preview-face/preview-small-v1/{attempt.id}-{'a' * 64}.jpg"
            ),
            byte_size=1024,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=3200,
            oriented_source_height=2000,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        preview_state.status = PhotoProcessingState.Status.SUCCEEDED
        preview_state.accepted_attempt = attempt
        preview_state.succeeded_at = timezone.now()
        preview_state.save(
            update_fields=["status", "accepted_attempt", "succeeded_at", "updated_at"]
        )

        state = request_face_embedding_enqueue(photo)

        assert state.current_job is not None
        self.assertEqual(
            (state.current_job.contract_version, state.current_job.processor_version), (2, 2)
        )
        self.assertEqual(
            state.current_job.input_fingerprint,
            {
                "object_key": derivative.final_key,
                "object_size": derivative.byte_size,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "preview-small-v1",
                "pixel_width": 1600,
                "pixel_height": 1000,
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
