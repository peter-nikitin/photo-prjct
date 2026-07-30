import importlib
from datetime import date
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)


class ProcessingModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="processor-owner")
        self.event = self.make_event("one")
        self.photo = self.make_private_photo("one", self.event)

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Event {suffix}",
            slug=f"event-{suffix}",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def make_private_photo(self, suffix: str, event: Event, **overrides) -> Photo:
        values = {
            "id": f"private-{suffix}",
            "event": event,
            "src": "",
            "uploaded_by": self.user,
            "original_key": f"originals/{suffix}",
            "original_filename": f"{suffix}.jpg",
            "original_size": 10,
            "original_content_type": "image/jpeg",
            "uploaded_at": timezone.now(),
        }
        values.update(overrides)
        return Photo.objects.create(**values)

    def make_run(self, *, event: Event | None = None, **overrides) -> EventProcessingRun:
        values = {
            "event": event or self.event,
            "contract_version": 1,
            "processor_type": "capture_metadata",
            "processor_version": 1,
            "configuration": {},
            "configuration_hash": "0" * 64,
        }
        values.update(overrides)
        return EventProcessingRun.objects.create(**values)

    def make_job(
        self,
        *,
        run: EventProcessingRun,
        photo: Photo | None = None,
        event: Event | None = None,
        **overrides,
    ) -> ProcessingJob:
        values = {
            "event": event or self.event,
            "run": run,
            "photo": photo or self.photo,
            "contract_version": 1,
            "processor_type": "capture_metadata",
            "processor_version": 1,
            "configuration": {},
            "configuration_hash": "0" * 64,
            "input_fingerprint": {},
        }
        values.update(overrides)
        return ProcessingJob.objects.create(**values)

    def test_status_vocabularies_are_explicit(self) -> None:
        self.assertEqual(
            {value for value, _ in PhotoProcessingState.Status.choices},
            {
                "not_requested",
                "queued",
                "processing",
                "retry_wait",
                "succeeded",
                "failed",
                "cancelled",
            },
        )
        self.assertEqual(
            {value for value, _ in EventProcessingRun.Status.choices},
            {"collecting", "sealed", "closed"},
        )
        self.assertEqual(
            {value for value, _ in ProcessingJob.Status.choices},
            {"queued", "processing", "retry_wait", "succeeded", "failed", "cancelled"},
        )
        self.assertEqual(
            {value for value, _ in ProcessingAttempt.Status.choices},
            {"in_progress", "succeeded", "failed", "expired", "stale"},
        )
        self.assertEqual(
            {value for value, _ in FaceProcessingAttemptArtifact.Status.choices},
            {"complete", "failed"},
        )
        self.assertEqual(
            {value for value, _ in PhotoFaceDetection.Status.choices},
            {"detected", "kept", "failed"},
        )

    def test_database_checks_reject_unknown_statuses(self) -> None:
        state = PhotoProcessingState.objects.get(
            photo=self.photo, processor_type="capture_metadata"
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                PhotoProcessingState.objects.filter(pk=state.pk).update(status="unknown")

    def test_photo_has_one_current_capture_metadata_state(self) -> None:
        state = PhotoProcessingState.objects.get(
            photo=self.photo, processor_type="capture_metadata"
        )
        self.assertEqual(state.status, PhotoProcessingState.Status.NOT_REQUESTED)
        with self.assertRaises(IntegrityError):
            PhotoProcessingState.objects.create(
                photo=self.photo,
                processor_type="capture_metadata",
                status=PhotoProcessingState.Status.NOT_REQUESTED,
            )

    def test_preview_first_photo_creates_explicit_preview_and_face_states(self) -> None:
        photo = self.make_private_photo(
            "preview-first",
            self.event,
            processing_generation="preview_first_v1",
            gallery_media_policy="preview_required",
        )

        self.assertEqual(
            set(
                PhotoProcessingState.objects.filter(photo=photo).values_list(
                    "processor_type", "status"
                )
            ),
            {
                ("capture_metadata", "not_requested"),
                ("generate_preview", "not_requested"),
                ("face_embedding", "not_requested"),
            },
        )

    def test_legacy_photo_does_not_create_preview_state(self) -> None:
        self.assertFalse(
            PhotoProcessingState.objects.filter(
                photo=self.photo,
                processor_type=GENERATE_PREVIEW_PROCESSOR,
            ).exists()
        )

    def make_preview_attempt(
        self,
        *,
        photo: Photo | None = None,
        processor_type: str = GENERATE_PREVIEW_PROCESSOR,
        **overrides,
    ) -> ProcessingAttempt:
        attempt_photo = photo or self.photo
        run = self.make_run(processor_type=processor_type)
        job = self.make_job(run=run, photo=attempt_photo, processor_type=processor_type)
        values = {
            "event": self.event,
            "run": run,
            "job": job,
            "photo": attempt_photo,
            "contract_version": 1,
            "processor_type": processor_type,
            "processor_version": 1,
            "configuration": {},
            "input_fingerprint": {},
            "status": ProcessingAttempt.Status.SUCCEEDED,
            "terminal_at": timezone.now(),
            "accepted": True,
        }
        values.update(overrides)
        return ProcessingAttempt.objects.create(**values)

    def preview_derivative_values(self, **overrides) -> dict[str, object]:
        values = {
            "photo": self.photo,
            "variant": "preview-small-v1",
            "final_key": "derivatives/preview-small-v1.jpg",
            "byte_size": 100,
            "content_type": "image/jpeg",
            "width": 1600,
            "height": 1000,
            "oriented_source_width": 3200,
            "oriented_source_height": 2000,
            "sha256": "a" * 64,
            "accepted_attempt": self.make_preview_attempt(),
        }
        values.update(overrides)
        return values

    def make_preview_derivative(self, **overrides) -> PhotoDerivative:
        values = self.preview_derivative_values(**overrides)
        return PhotoDerivative.objects.create(**values)

    def test_derivative_is_unique_per_photo_variant_and_protects_its_attempt(self) -> None:
        derivative = self.make_preview_derivative()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_preview_derivative(
                final_key="derivatives/second-preview-small-v1.jpg",
                accepted_attempt=self.make_preview_attempt(),
            )
        with self.assertRaises(ProtectedError):
            derivative.accepted_attempt.delete()

    def test_derivative_cannot_mutate_after_publication(self) -> None:
        derivative = self.make_preview_derivative()
        derivative.width = 800

        with self.assertRaises(ValidationError):
            derivative.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PhotoDerivative.objects.filter(pk=derivative.pk).update(width=800)

    def test_derivative_cannot_be_deleted_after_publication(self) -> None:
        derivative = self.make_preview_derivative()

        with self.assertRaises(ValidationError):
            derivative.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PhotoDerivative.objects.filter(pk=derivative.pk).delete()

    def test_derivative_model_validation_requires_an_accepted_preview_attempt(self) -> None:
        other_photo = self.make_private_photo("other", self.event)
        invalid_attempts = {
            "wrong_photo": self.make_preview_attempt(photo=other_photo),
            "unaccepted": self.make_preview_attempt(accepted=False),
            "failed": self.make_preview_attempt(status=ProcessingAttempt.Status.FAILED),
            "stale": self.make_preview_attempt(status=ProcessingAttempt.Status.STALE),
            "wrong_processor": self.make_preview_attempt(processor_type="capture_metadata"),
        }

        for reason, attempt in invalid_attempts.items():
            with self.subTest(reason=reason):
                derivative = PhotoDerivative(
                    **self.preview_derivative_values(
                        variant=f"preview-small-v1-{reason}",
                        final_key=f"derivatives/{reason}.jpg",
                        accepted_attempt=attempt,
                    )
                )
                with self.assertRaises(ValidationError):
                    derivative.full_clean()

    def test_derivative_database_requires_an_accepted_preview_attempt(self) -> None:
        other_photo = self.make_private_photo("other", self.event)
        invalid_attempts = {
            "wrong_photo": self.make_preview_attempt(photo=other_photo),
            "unaccepted": self.make_preview_attempt(accepted=False),
            "failed": self.make_preview_attempt(status=ProcessingAttempt.Status.FAILED),
            "stale": self.make_preview_attempt(status=ProcessingAttempt.Status.STALE),
            "wrong_processor": self.make_preview_attempt(processor_type="capture_metadata"),
        }

        for reason, attempt in invalid_attempts.items():
            with self.subTest(reason=reason), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    PhotoDerivative.objects.create(
                        **self.preview_derivative_values(
                            variant=f"preview-small-v1-{reason}",
                            final_key=f"derivatives/{reason}.jpg",
                            accepted_attempt=attempt,
                        )
                    )

    def test_derivative_insert_trigger_blocks_bulk_and_direct_sql_inserts(self) -> None:
        attempt = self.make_preview_attempt(accepted=False)
        derivative = PhotoDerivative(
            **self.preview_derivative_values(
                variant="preview-small-v1-bulk",
                final_key="derivatives/bulk.jpg",
                accepted_attempt=attempt,
            )
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PhotoDerivative.objects.bulk_create([derivative])
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO processing_photoderivative (
                    photo_id, variant, final_key, byte_size, content_type, width, height,
                    oriented_source_width, oriented_source_height, sha256, accepted_attempt_id,
                    published_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                [
                    self.photo.pk,
                    "preview-small-v1-direct",
                    "derivatives/direct.jpg",
                    100,
                    "image/jpeg",
                    1600,
                    1000,
                    3200,
                    2000,
                    "a" * 64,
                    attempt.pk,
                ],
            )

    def test_exact_job_identity_is_unique(self) -> None:
        run = self.make_run()
        self.make_job(run=run)
        with self.assertRaises(IntegrityError):
            self.make_job(run=run)

    def test_job_configurations_belong_to_separate_compatible_runs(self) -> None:
        default_run = self.make_run()
        configured_run = self.make_run(
            configuration={"timezone": "UTC"},
            configuration_hash="1" * 64,
        )
        default_job = self.make_job(run=default_run)
        configured_job = self.make_job(
            run=configured_run,
            configuration={"timezone": "UTC"},
            configuration_hash="1" * 64,
        )
        self.assertNotEqual(default_job.run_id, configured_job.run_id)

    def test_job_must_match_its_run_processor_identity_at_database_boundary(self) -> None:
        run = self.make_run()
        mismatches: dict[str, Any] = {
            "contract_version": 2,
            "processor_type": "another_processor",
            "processor_version": 2,
            "configuration": {"timezone": "UTC"},
            "configuration_hash": "1" * 64,
        }
        for field_name, value in mismatches.items():
            with self.subTest(field_name=field_name), transaction.atomic():
                with self.assertRaises(IntegrityError):
                    self.make_job(run=run, **{field_name: value})

    def test_run_identity_cannot_change_after_a_job_exists(self) -> None:
        run = self.make_run()
        self.make_job(run=run)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                EventProcessingRun.objects.filter(pk=run.pk).update(processor_version=2)

    def test_job_identity_cannot_change_after_an_attempt_exists(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingJob.objects.filter(pk=job.pk).update(processor_version=2)

    def test_photo_event_cannot_change_after_a_processing_job_exists(self) -> None:
        run = self.make_run()
        self.make_job(run=run)
        other_event = self.make_event("two")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Photo.objects.filter(pk=self.photo.pk).update(event=other_event)

    def test_sealed_run_retains_unclaimed_job_after_its_state_is_deleted(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        state = PhotoProcessingState.objects.get(
            photo=self.photo, processor_type="capture_metadata"
        )
        state.status = PhotoProcessingState.Status.QUEUED
        state.current_run = run
        state.current_job = job
        state.save(update_fields=["status", "current_run", "current_job", "updated_at"])
        state.delete()
        EventProcessingRun.objects.filter(pk=run.pk).update(
            status=EventProcessingRun.Status.SEALED,
            sealed_at=timezone.now(),
        )

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                job.delete()

        self.assertTrue(ProcessingJob.objects.filter(pk=job.pk).exists())
        self.assertEqual(run.jobs.count(), 1)

    def test_collecting_run_allows_unclaimed_job_deletion(self) -> None:
        job = self.make_job(run=self.make_run())

        job.delete()

        self.assertFalse(ProcessingJob.objects.filter(pk=job.pk).exists())

    def test_terminal_attempt_evidence_cannot_be_mutated(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at="2026-07-29T00:00:00Z",
            result={"capture_time": None},
        )
        attempt.result = {"capture_time": "2026-07-29T10:00:00Z"}
        with self.assertRaises(ValidationError):
            attempt.save()

    def test_job_rejects_foreign_event_ownership_at_database_boundary(self) -> None:
        other_event = self.make_event("two")
        foreign_photo = self.make_private_photo("two", other_event)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                self.make_job(run=self.make_run(), photo=foreign_photo)

    def test_attempt_rejects_same_event_job_for_another_photo_at_database_boundary(self) -> None:
        other_photo = self.make_private_photo("two", self.event)
        run = self.make_run()
        job = self.make_job(run=run)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingAttempt.objects.create(
                    event=self.event,
                    run=run,
                    job=job,
                    photo=other_photo,
                    contract_version=1,
                    processor_type="capture_metadata",
                    processor_version=1,
                    configuration={},
                    input_fingerprint={},
                )

    def test_state_rejects_same_event_job_for_another_photo_at_database_boundary(self) -> None:
        other_photo = self.make_private_photo("two", self.event)
        run = self.make_run()
        other_job = self.make_job(run=run, photo=other_photo)
        state = PhotoProcessingState.objects.get(
            photo=self.photo, processor_type="capture_metadata"
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                PhotoProcessingState.objects.filter(pk=state.pk).update(
                    current_run=run,
                    current_job=other_job,
                )

    def test_state_rejects_same_event_attempt_for_another_photo_at_database_boundary(self) -> None:
        other_photo = self.make_private_photo("two", self.event)
        run = self.make_run()
        other_job = self.make_job(run=run, photo=other_photo)
        other_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=other_job,
            photo=other_photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
        )
        state = PhotoProcessingState.objects.get(
            photo=self.photo, processor_type="capture_metadata"
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                PhotoProcessingState.objects.filter(pk=state.pk).update(
                    current_attempt=other_attempt,
                )

    def test_terminal_status_and_timestamp_must_agree(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        base = {
            "event": self.event,
            "run": run,
            "job": job,
            "photo": self.photo,
            "contract_version": 1,
            "processor_type": "capture_metadata",
            "processor_version": 1,
            "configuration": {},
            "input_fingerprint": {},
        }
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingAttempt.objects.create(
                    **base,
                    status=ProcessingAttempt.Status.SUCCEEDED,
                )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingAttempt.objects.create(
                    **base,
                    status=ProcessingAttempt.Status.IN_PROGRESS,
                    terminal_at="2026-07-29T00:00:00Z",
                )

    def test_terminal_attempt_rejects_queryset_bulk_update_and_delete(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at="2026-07-29T00:00:00Z",
            result={"capture_time": None},
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingAttempt.objects.filter(pk=attempt.pk).update(result={"capture_time": "x"})
        attempt.result = {"capture_time": "x"}
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingAttempt.objects.bulk_update([attempt], ["result"])
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProcessingAttempt.objects.filter(pk=attempt.pk).delete()

    def test_configuration_and_result_payloads_are_bounded(self) -> None:
        run = self.make_run()
        run.configuration = {"payload": "x" * 16_385}
        with self.assertRaises(ValidationError):
            run.full_clean()

        job = self.make_job(run=run)
        attempt = ProcessingAttempt(
            event=self.event,
            run=job.run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            result={"payload": "x" * 16_385},
        )
        with self.assertRaises(ValidationError):
            attempt.full_clean()

        terminal_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at="2026-07-29T00:00:00Z",
            result={"capture_time": None},
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(
            attempt=terminal_attempt,
            status=FaceProcessingAttemptArtifact.Status.COMPLETE,
            feature_payload={"payload": "ok"},
            quality_payload={"payload": "ok"},
        )
        detection = PhotoFaceDetection(
            attempt=terminal_attempt,
            artifact=artifact,
            face_index=0,
            status=PhotoFaceDetection.Status.DETECTED,
            geometry={"payload": "ok"},
            features={"payload": "ok"},
        )
        with self.assertRaises(ValidationError):
            detection.geometry = {"payload": "x" * 16_385}
            detection.full_clean()

        embedding = FaceEmbedding(
            detection=PhotoFaceDetection.objects.create(
                attempt=terminal_attempt,
                artifact=artifact,
                face_index=1,
                status=PhotoFaceDetection.Status.DETECTED,
                geometry={"payload": "ok"},
                features={"payload": "ok"},
            ),
            model_version="v1",
            vector=["x" * 16_385],
            metadata={"payload": "ok"},
        )
        with self.assertRaises(ValidationError):
            embedding.full_clean()

    def test_face_feature_layer_enforces_attempt_face_index_uniqueness(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at="2026-07-29T00:00:00Z",
            result={"capture_time": None},
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(
            attempt=attempt,
            status=FaceProcessingAttemptArtifact.Status.COMPLETE,
            feature_payload={"detector": "unit"},
            quality_payload={"passed": True},
        )
        PhotoFaceDetection.objects.create(
            attempt=attempt,
            artifact=artifact,
            face_index=0,
            status=PhotoFaceDetection.Status.DETECTED,
            geometry={"x": 0, "y": 0, "w": 1, "h": 1},
            features={"source": "test"},
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                PhotoFaceDetection.objects.create(
                    attempt=attempt,
                    artifact=artifact,
                    face_index=0,
                    status=PhotoFaceDetection.Status.KEPT,
                    geometry={"x": 0, "y": 0, "w": 1, "h": 1},
                    features={"source": "duplicate"},
                )

    def test_face_feature_layer_connects_artifact_and_embedding_to_attempt(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at="2026-07-29T00:00:00Z",
            result={"capture_time": None},
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(
            attempt=attempt,
            status=FaceProcessingAttemptArtifact.Status.COMPLETE,
            feature_payload={"detector": "unit"},
            quality_payload={"passed": True},
        )
        detection = PhotoFaceDetection.objects.create(
            attempt=attempt,
            artifact=artifact,
            face_index=1,
            status=PhotoFaceDetection.Status.KEPT,
            geometry={"x": 1, "y": 1, "w": 2, "h": 2},
            features={"source": "link"},
        )
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version="v1",
            vector=[0.1, 0.2],
            metadata={"norm": 1.0},
        )

        self.assertEqual(detection.artifact_id, artifact.id)
        self.assertEqual(embedding.detection_id, detection.id)
        self.assertEqual(
            PhotoFaceDetection.objects.filter(
                attempt=attempt, status=PhotoFaceDetection.Status.KEPT
            ).count(),
            1,
        )

    def test_face_feature_layer_rejects_non_terminal_parent_attempts(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.IN_PROGRESS,
            result={},
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                FaceProcessingAttemptArtifact.objects.create(
                    attempt=attempt,
                    status=FaceProcessingAttemptArtifact.Status.COMPLETE,
                    feature_payload={"detector": "unit"},
                    quality_payload={"passed": False},
                )

    def test_face_feature_layer_is_immutable_for_terminal_parent_attempt(self) -> None:
        run = self.make_run()
        job = self.make_job(run=run)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.FAILED,
            terminal_at="2026-07-29T00:00:00Z",
            result={"capture_time": None},
            error_code="timeout",
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(
            attempt=attempt,
            status=FaceProcessingAttemptArtifact.Status.FAILED,
            feature_payload={"detector": "unit"},
            quality_payload={"passed": False},
        )
        detection = PhotoFaceDetection.objects.create(
            attempt=attempt,
            artifact=artifact,
            face_index=0,
            status=PhotoFaceDetection.Status.FAILED,
            geometry={"x": 0, "y": 0, "w": 1, "h": 1},
            features={"source": "blocked"},
        )
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version="v1",
            vector=[0.1],
            metadata={},
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                FaceProcessingAttemptArtifact.objects.filter(pk=artifact.pk).delete()
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                PhotoFaceDetection.objects.filter(pk=detection.pk).update(
                    status=PhotoFaceDetection.Status.DETECTED
                )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                FaceEmbedding.objects.filter(pk=embedding.pk).update(vector=[0.1, 0.2])


class ProcessingInitialMigrationTests(TransactionTestCase):
    migrate_from = [("picflow", "0005_validate_photo_private_original_constraints")]
    migrate_to = [("processing", "0001_initial")]
    unapply_processing = [("processing", None)]

    def test_existing_photo_gets_not_requested_state_and_reversal_preserves_photo(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.unapply_processing)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Event = old_apps.get_model("picflow", "Event")
        Photo = old_apps.get_model("picflow", "Photo")
        event = Event.objects.create(
            name="Migrated event",
            slug="migrated-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        Photo.objects.create(id="legacy", event=event, src="photos/legacy.jpg")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        PhotoProcessingState = new_apps.get_model("processing", "PhotoProcessingState")
        self.assertEqual(
            PhotoProcessingState.objects.filter(photo_id="legacy")
            .values_list("processor_type", "status")
            .get(),
            ("capture_metadata", "not_requested"),
        )

        executor.migrate(self.unapply_processing)
        reverse_apps = executor.loader.project_state(self.migrate_from).apps
        ReversePhoto = reverse_apps.get_model("picflow", "Photo")
        self.assertTrue(ReversePhoto.objects.filter(pk="legacy").exists())

        MigrationExecutor(connection).migrate(self.migrate_to)


class ProcessingMigrationFunctionTests(TestCase):
    def test_legacy_backfill_consumes_photos_in_bounded_batches(self) -> None:
        migration = importlib.import_module("processing.migrations.0001_initial")
        batches: list[int] = []

        class FakePhotoManager:
            def values_list(self, field_name: str, flat: bool):
                self.field_name = field_name
                self.flat = flat
                return self

            def iterator(self):
                return iter(range(501))

        class FakePhoto:
            objects = FakePhotoManager()

        class FakeStateManager:
            def bulk_create(self, states, **kwargs) -> None:
                batches.append(len(states))
                self.kwargs = kwargs

        class FakeState:
            objects = FakeStateManager()

            def __init__(self, *, photo_id: int, processor_type: str) -> None:
                self.photo_id = photo_id
                self.processor_type = processor_type

        class FakeApps:
            def get_model(self, app_label: str, model_name: str):
                models = {
                    ("picflow", "Photo"): FakePhoto,
                    ("processing", "PhotoProcessingState"): FakeState,
                }
                return models[(app_label, model_name)]

        migration.create_legacy_capture_metadata_states(FakeApps(), schema_editor=None)

        self.assertEqual(batches, [500, 1])


class ProcessingFaceEmbeddingMigrationTests(TransactionTestCase):
    migrate_from = [("processing", "0001_initial")]
    migrate_to = [("processing", "0002_add_face_embedding_schema")]

    def test_face_embedding_schema_migrates_forward_and_back_without_schema_errors(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        for model_name in (
            "faceprocessingattemptartifact",
            "photofacedetection",
            "faceembedding",
        ):
            self.assertIn(model_name, migrated_apps.all_models["processing"])
            self.assertTrue(
                migrated_apps.all_models["processing"][model_name]._meta.db_table,
            )

        executor.migrate(self.migrate_from)
        reverted_apps = executor.loader.project_state(self.migrate_from).apps
        for model_name in (
            "faceprocessingattemptartifact",
            "photofacedetection",
            "faceembedding",
        ):
            self.assertNotIn(model_name, reverted_apps.all_models.get("processing", {}))


class ProcessingPreviewDerivativeMigrationTests(TransactionTestCase):
    migrate_from = [("processing", "0002_add_face_embedding_schema")]
    migrate_to = [("processing", "0003_add_preview_derivative_schema")]

    def test_preview_derivative_schema_migrates_forward_and_back_without_schema_errors(
        self,
    ) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        self.assertIn("photoderivative", migrated_apps.all_models["processing"])

        executor.migrate(self.migrate_from)
        reverted_apps = executor.loader.project_state(self.migrate_from).apps
        self.assertNotIn("photoderivative", reverted_apps.all_models.get("processing", {}))


class ProcessingFaceIndexNameMigrationTests(TransactionTestCase):
    migrate_from = [("processing", "0003_add_preview_derivative_schema")]
    migrate_to = [("processing", "0004_shorten_face_index_names")]

    def test_canonical_face_index_names_migrate_to_the_current_names(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        MigrationExecutor(connection).migrate(self.migrate_to)

        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)::text", ["proc_face_det_attempt_idx"])
            self.assertEqual(cursor.fetchone()[0], "proc_face_det_attempt_idx")
            cursor.execute("SELECT to_regclass(%s)::text", ["proc_face_embed_det_idx"])
            self.assertEqual(cursor.fetchone()[0], "proc_face_embed_det_idx")

    def test_legacy_face_index_names_migrate_to_the_current_names(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER INDEX proc_face_detection_attempt_idx RENAME TO proc_face_detect_attempt_idx"
            )
            cursor.execute(
                "ALTER INDEX proc_face_embedding_detection_idx "
                "RENAME TO proc_face_embed_detection_idx"
            )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        PhotoFaceDetection = migrated_apps.get_model("processing", "PhotoFaceDetection")
        FaceEmbedding = migrated_apps.get_model("processing", "FaceEmbedding")
        self.assertEqual(
            [index.name for index in PhotoFaceDetection._meta.indexes],
            ["proc_face_det_attempt_idx", "proc_face_detection_status_idx"],
        )
        self.assertEqual(
            [index.name for index in FaceEmbedding._meta.indexes],
            ["proc_face_embed_det_idx"],
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)::text", ["proc_face_det_attempt_idx"])
            self.assertEqual(cursor.fetchone()[0], "proc_face_det_attempt_idx")
            cursor.execute("SELECT to_regclass(%s)::text", ["proc_face_embed_det_idx"])
            self.assertEqual(cursor.fetchone()[0], "proc_face_embed_det_idx")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)::text", ["proc_face_detection_attempt_idx"])
            self.assertEqual(cursor.fetchone()[0], "proc_face_detection_attempt_idx")
            cursor.execute("SELECT to_regclass(%s)::text", ["proc_face_embedding_detection_idx"])
            self.assertEqual(cursor.fetchone()[0], "proc_face_embedding_detection_idx")

    def test_current_face_index_names_are_idempotent(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER INDEX proc_face_detection_attempt_idx RENAME TO proc_face_det_attempt_idx"
            )
            cursor.execute(
                "ALTER INDEX proc_face_embedding_detection_idx RENAME TO proc_face_embed_det_idx"
            )

        MigrationExecutor(connection).migrate(self.migrate_to)
