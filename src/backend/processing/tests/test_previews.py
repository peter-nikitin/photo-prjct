from __future__ import annotations

import hashlib
from datetime import date, timedelta
from queue import Queue
from threading import Barrier, Lock, Thread
from threading import Event as ThreadEvent
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo

from processing.contracts import AttemptCompletion, CompletionConflict
from processing.models import (
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services import jobs
from processing.services.enrollment import GENERATE_PREVIEW_CONFIGURATION, request_processor
from processing.services.jobs import claim_job
from processing.services.previews import complete_preview_attempt, preview_final_key
from processing.storage import ObjectConflict, ObjectMismatch, PreviewObject


class FakePreviewStorage:
    """In-memory external-object boundary; service transitions remain real Django transactions."""

    def __init__(self, object: PreviewObject, *, staging_barrier: Barrier | None = None) -> None:
        self.object = object
        self.final_object: PreviewObject | None = None
        self.copy_calls = 0
        self.promote_final_keys: list[str] = []
        self.raise_after_copy = False
        self._lock = Lock()
        self._staging_barrier = staging_barrier

    def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
        if not key.startswith("derivatives/") and self._staging_barrier is not None:
            self._staging_barrier.wait(timeout=5)
        if key.startswith("derivatives/"):
            with self._lock:
                final_object = self.final_object
            if final_object is None:
                from ingestion.storage import ObjectMissing

                raise ObjectMissing()
            return final_object
        return self.object

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject:
        with self._lock:
            if self.final_object is not None:
                raise ObjectConflict()
            self.copy_calls += 1
            self.promote_final_keys.append(final_key)
            self.final_object = self.object
            if self.raise_after_copy:
                self.raise_after_copy = False
                raise RuntimeError("interrupted after copy")
            return self.final_object


class _PreviewPublicationFixture:
    """The production break caught here exposes an unverified or stale preview as accepted media."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="preview-publication-owner")
        self.event = Event.objects.create(
            name="Preview publication event",
            slug="preview-publication-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def _claim(self, identifier: str = "preview-publication"):
        photo = Photo.objects.create(
            id=identifier,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_filename="preview.jpg",
            original_size=20,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
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
        return photo, claim_job(
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            worker_build="preview-worker",
        )

    def _result(self, object: PreviewObject, **overrides: object) -> dict[str, object]:
        return {
            "variant": "preview-small-v1",
            "content_type": "image/jpeg",
            "byte_size": object.byte_size,
            "width": object.width,
            "height": object.height,
            "oriented_source_width": 3200,
            "oriented_source_height": 2000,
            "sha256": object.sha256,
            "upload_ms": 4,
            "warnings": [],
        } | overrides

    def _stored_object(self) -> PreviewObject:
        content = b"verified-preview-bytes"
        return PreviewObject(
            etag_wire='"preview-etag"',
            etag_value="preview-etag",
            byte_size=len(content),
            content_type="image/jpeg",
            sha256=hashlib.sha256(content).hexdigest(),
            width=1600,
            height=1000,
        )


class PreviewPublicationServiceTests(_PreviewPublicationFixture, TestCase):
    def test_current_attempt_verifies_promotes_and_atomically_publishes_an_immutable_derivative(
        self,
    ) -> None:
        photo, claimed = self._claim()
        object = self._stored_object()

        completion = complete_preview_attempt(
            claimed.attempt.id,
            result=self._result(object),
            storage=FakePreviewStorage(object),
            total_duration_ms=7,
        )

        derivative = PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        self.assertFalse(completion.idempotent)
        self.assertEqual(derivative.accepted_attempt_id, claimed.attempt.id)
        self.assertEqual(derivative.sha256, object.sha256)
        self.assertEqual(
            derivative.final_key,
            preview_final_key(
                photo_id=photo.id,
                attempt_id=claimed.attempt.id,
                sha256=object.sha256,
            ),
        )
        self.assertEqual(state.status, PhotoProcessingState.Status.SUCCEEDED)
        self.assertEqual(state.accepted_attempt_id, claimed.attempt.id)

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_accepted_preview_publication_queues_the_preview_backed_face_job_once(self) -> None:
        photo, claimed = self._claim("preview-enroll-face")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        object = self._stored_object()

        complete_preview_attempt(
            claimed.attempt.id, result=self._result(object), storage=FakePreviewStorage(object)
        )
        complete_preview_attempt(
            claimed.attempt.id, result=self._result(object), storage=FakePreviewStorage(object)
        )

        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        self.assertEqual(face.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(
            (face.current_job.contract_version, face.current_job.processor_version), (2, 2)
        )
        self.assertEqual(face.current_job.input_fingerprint["media_kind"], "preview-small-v1")
        self.assertEqual(
            ProcessingJob.objects.filter(photo=photo, processor_type="face_embedding").count(), 1
        )

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_face_enqueue_failure_rolls_back_preview_acceptance_and_allows_retry(self) -> None:
        """Catch a committed preview whose duplicate receipt cannot retry face enrollment."""
        photo, claimed = self._claim("preview-face-atomic")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        object = self._stored_object()
        storage = FakePreviewStorage(object)

        with (
            patch(
                "processing.services.enrollment.request_face_embedding_enqueue",
                side_effect=RuntimeError("face enrollment failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "face enrollment failed"),
        ):
            complete_preview_attempt(
                claimed.attempt.id,
                result=self._result(object),
                storage=storage,
            )

        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())
        self.assertFalse(
            ProcessingJob.objects.filter(photo=photo, processor_type="face_embedding").exists()
        )

        completion = complete_preview_attempt(
            claimed.attempt.id,
            result=self._result(object),
            storage=storage,
        )

        self.assertFalse(completion.idempotent)
        self.assertTrue(PhotoDerivative.objects.filter(photo=photo).exists())
        self.assertTrue(
            ProcessingJob.objects.filter(photo=photo, processor_type="face_embedding").exists()
        )

    def test_retry_converges_after_copy_succeeded_before_the_database_transaction(self) -> None:
        photo, claimed = self._claim("preview-recovery")
        object = self._stored_object()
        storage = FakePreviewStorage(object)
        storage.raise_after_copy = True

        with self.assertRaises(RuntimeError):
            complete_preview_attempt(
                claimed.attempt.id, result=self._result(object), storage=storage
            )
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

        completion = complete_preview_attempt(
            claimed.attempt.id, result=self._result(object), storage=storage
        )

        self.assertFalse(completion.idempotent)
        self.assertEqual(storage.copy_calls, 1)
        self.assertEqual(PhotoDerivative.objects.get(photo=photo).sha256, object.sha256)

    def test_duplicate_completion_is_idempotent_but_a_conflicting_terminal_payload_is_rejected(
        self,
    ) -> None:
        _, claimed = self._claim("preview-duplicate")
        object = self._stored_object()
        storage = FakePreviewStorage(object)
        result = self._result(object)

        complete_preview_attempt(claimed.attempt.id, result=result, storage=storage)
        repeated = complete_preview_attempt(claimed.attempt.id, result=result, storage=storage)

        self.assertTrue(repeated.idempotent)
        with self.assertRaises(CompletionConflict):
            complete_preview_attempt(
                claimed.attempt.id,
                result=result | {"sha256": "b" * 64},
                storage=storage,
            )
        derivative = PhotoDerivative.objects.get()
        self.assertEqual(derivative.sha256, object.sha256)
        self.assertEqual(storage.promote_final_keys, [derivative.final_key])

    def test_content_addressed_final_keys_separate_conflicting_declared_checksums(self) -> None:
        _, claimed = self._claim("preview-content-addressed")
        first_checksum = "a" * 64
        conflicting_checksum = "b" * 64

        first_key = preview_final_key(
            photo_id="preview-content-addressed",
            attempt_id=claimed.attempt.id,
            sha256=first_checksum,
        )
        conflicting_key = preview_final_key(
            photo_id="preview-content-addressed",
            attempt_id=claimed.attempt.id,
            sha256=conflicting_checksum,
        )

        self.assertEqual(
            first_key,
            (
                "derivatives/previews/preview-content-addressed/preview-small-v1/"
                f"{claimed.attempt.id}-{first_checksum}.jpg"
            ),
        )
        self.assertNotEqual(first_key, conflicting_key)

    def test_existing_mismatching_final_is_rejected_without_a_copy_or_publication(self) -> None:
        photo, claimed = self._claim("preview-conflict")
        object = self._stored_object()
        storage = FakePreviewStorage(object)
        storage.final_object = PreviewObject(
            etag_wire='"different"',
            etag_value="different",
            byte_size=object.byte_size,
            content_type="image/jpeg",
            sha256="0" * 64,
            width=1600,
            height=1000,
        )

        with self.assertRaises(CompletionConflict):
            complete_preview_attempt(
                claimed.attempt.id, result=self._result(object), storage=storage
            )

        self.assertEqual(storage.copy_calls, 0)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_source_dimension_mismatch_is_a_permanent_output_contract_failure(self) -> None:
        photo, claimed = self._claim("preview-source-dimensions")
        object = self._stored_object()

        completion = complete_preview_attempt(
            claimed.attempt.id,
            result=self._result(object, oriented_source_width=3199),
            storage=FakePreviewStorage(object),
        )

        self.assertEqual(completion.attempt.status, ProcessingAttempt.Status.FAILED)
        self.assertEqual(completion.attempt.error_code, "output_contract_violation")
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_expired_preview_completion_is_retained_without_promoting_or_publishing(self) -> None:
        photo, claimed = self._claim("preview-expired")
        object = self._stored_object()
        storage = FakePreviewStorage(object)
        ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(
            lease_expires_at=timezone.now()
        )

        completion = complete_preview_attempt(
            claimed.attempt.id,
            result=self._result(object),
            storage=storage,
        )

        self.assertTrue(completion.stale)
        self.assertEqual(storage.copy_calls, 0)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_lease_expiring_during_copy_cannot_publish_after_storage_work(self) -> None:
        photo, claimed = self._claim("preview-expire-during-copy")
        object = self._stored_object()

        class ExpiringStorage(FakePreviewStorage):
            def promote(self, **kwargs):
                promoted = super().promote(**kwargs)
                ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(
                    lease_expires_at=timezone.now() - timedelta(seconds=1)
                )
                return promoted

        completion = complete_preview_attempt(
            claimed.attempt.id,
            result=self._result(object),
            storage=ExpiringStorage(object),
        )

        claimed.attempt.refresh_from_db()
        self.assertTrue(completion.stale)
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_storage_failure_after_lease_expiry_uses_fresh_time_for_late_receipt(self) -> None:
        photo, claimed = self._claim("preview-expire-during-verify")
        object = self._stored_object()

        class ExpiringFailureStorage(FakePreviewStorage):
            def verify(self, **kwargs):
                ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(
                    lease_expires_at=timezone.now() - timedelta(seconds=1)
                )
                from ingestion.storage import ObjectMismatch

                raise ObjectMismatch()

        completion = complete_preview_attempt(
            claimed.attempt.id,
            result=self._result(object),
            storage=ExpiringFailureStorage(object),
        )

        claimed.attempt.refresh_from_db()
        receipt = ProcessingLateReceipt.objects.get(attempt=claimed.attempt)
        self.assertTrue(completion.stale)
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(receipt.payload["outcome"], "failure")
        self.assertEqual(receipt.payload["error_code"], "output_contract_violation")
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_stale_completion_is_recorded_without_publishing_a_derivative(self) -> None:
        photo, claimed = self._claim("preview-stale")
        object = self._stored_object()
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        successor = ProcessingAttempt.objects.create(
            event=self.event,
            run=claimed.job.run,
            job=claimed.job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=claimed.job.configuration,
            input_fingerprint=claimed.job.input_fingerprint,
            claimed_at=timezone.now(),
            heartbeat_at=timezone.now(),
            lease_expires_at=timezone.now() + timedelta(seconds=30),
        )
        state.current_attempt = successor
        state.save(update_fields=["current_attempt", "updated_at"])

        completion = complete_preview_attempt(
            claimed.attempt.id, result=self._result(object), storage=FakePreviewStorage(object)
        )

        self.assertTrue(completion.stale)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())
        state.refresh_from_db()
        self.assertEqual(state.current_attempt_id, successor.id)


class PreviewPublicationConcurrencyTests(_PreviewPublicationFixture, TransactionTestCase):
    def _complete_in_thread(
        self,
        attempt_id: UUID,
        result: dict[str, object],
        storage: FakePreviewStorage,
        completions: Queue[AttemptCompletion],
        failures: Queue[BaseException],
    ) -> Thread:
        def complete() -> None:
            close_old_connections()
            try:
                completions.put(
                    complete_preview_attempt(attempt_id, result=result, storage=storage)
                )
            except BaseException as error:  # noqa: BLE001
                failures.put(error)
            finally:
                close_old_connections()

        return Thread(target=complete)

    def test_simultaneous_matching_completions_publish_one_accepted_content_addressed_derivative(
        self,
    ) -> None:
        photo, claimed = self._claim("preview-concurrent")
        object = self._stored_object()
        storage = FakePreviewStorage(object, staging_barrier=Barrier(2))
        results: Queue[AttemptCompletion] = Queue()
        failures: Queue[BaseException] = Queue()

        def complete() -> None:
            close_old_connections()
            try:
                results.put(
                    complete_preview_attempt(
                        claimed.attempt.id,
                        result=self._result(object),
                        storage=storage,
                    )
                )
            except BaseException as error:  # noqa: BLE001
                failures.put(error)
            finally:
                close_old_connections()

        workers = [Thread(target=complete), Thread(target=complete)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        completions = [results.get_nowait(), results.get_nowait()]
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertTrue(failures.empty())
        self.assertEqual(sum(not completion.idempotent for completion in completions), 1)
        derivative = PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")
        self.assertEqual(derivative.accepted_attempt_id, claimed.attempt.id)
        self.assertEqual(derivative.sha256, object.sha256)
        self.assertEqual(storage.copy_calls, 1)
        self.assertEqual(
            storage.promote_final_keys,
            [
                preview_final_key(
                    photo_id=photo.id,
                    attempt_id=claimed.attempt.id,
                    sha256=object.sha256,
                )
            ],
        )

    def test_lock_wait_past_expiry_records_success_as_late_without_publishing(self) -> None:
        photo, claimed = self._claim("preview-success-lock-wait")
        object = self._stored_object()
        before = timezone.now()
        expiry = before + timedelta(seconds=1)
        after_expiry = expiry + timedelta(seconds=1)
        ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(lease_expires_at=expiry)
        publication_phase = ThreadEvent()
        final_verified = ThreadEvent()
        allow_publish = ThreadEvent()
        lock_wait_started = ThreadEvent()
        failures: Queue[BaseException] = Queue()

        class LockWaitingStorage(FakePreviewStorage):
            def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
                verified = super().verify(key=key, max_bytes=max_bytes)
                if key.startswith("derivatives/"):
                    final_verified.set()
                    if not allow_publish.wait(timeout=5):
                        raise AssertionError("test did not allow publication after acquiring locks")
                return verified

        use_expired_time = ThreadEvent()
        original_locked_context = jobs._locked_context

        def observed_locked_context(attempt_id: UUID):
            if publication_phase.is_set():
                lock_wait_started.set()
            return original_locked_context(attempt_id)

        def controlled_now() -> timezone.datetime:
            return after_expiry if use_expired_time.is_set() else before

        completions: Queue[AttemptCompletion] = Queue()
        storage = LockWaitingStorage(object)
        worker = self._complete_in_thread(
            claimed.attempt.id, self._result(object), storage, completions, failures
        )
        with (
            patch("processing.services.previews.timezone.now", side_effect=controlled_now),
            patch.object(jobs, "_locked_context", side_effect=observed_locked_context),
        ):
            worker.start()
            self.assertTrue(final_verified.wait(timeout=5))
            with transaction.atomic():
                jobs._locked_context(claimed.attempt.id)
                publication_phase.set()
                allow_publish.set()
                use_expired_time.set()
            self.assertTrue(lock_wait_started.wait(timeout=5))
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertTrue(failures.empty())
        completion = completions.get_nowait()
        claimed.attempt.refresh_from_db()
        receipt = ProcessingLateReceipt.objects.get(attempt=claimed.attempt)
        self.assertTrue(completion.stale)
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(receipt.payload["outcome"], "success")
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_lock_wait_past_expiry_records_post_io_failure_as_late(self) -> None:
        photo, claimed = self._claim("preview-failure-lock-wait")
        object = self._stored_object()
        before = timezone.now()
        expiry = before + timedelta(seconds=1)
        after_expiry = expiry + timedelta(seconds=1)
        ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(lease_expires_at=expiry)
        publication_phase = ThreadEvent()
        verification_failed = ThreadEvent()
        allow_failure = ThreadEvent()
        lock_wait_started = ThreadEvent()
        failures: Queue[BaseException] = Queue()

        class LockWaitingFailureStorage(FakePreviewStorage):
            def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
                verification_failed.set()
                if not allow_failure.wait(timeout=5):
                    raise AssertionError("test did not allow failure after acquiring locks")
                raise ObjectMismatch()

        use_expired_time = ThreadEvent()
        original_locked_context = jobs._locked_context

        def observed_locked_context(attempt_id: UUID):
            if publication_phase.is_set():
                lock_wait_started.set()
            return original_locked_context(attempt_id)

        def controlled_now() -> timezone.datetime:
            return after_expiry if use_expired_time.is_set() else before

        completions: Queue[AttemptCompletion] = Queue()
        storage = LockWaitingFailureStorage(object)
        worker = self._complete_in_thread(
            claimed.attempt.id, self._result(object), storage, completions, failures
        )
        with (
            patch("processing.services.previews.timezone.now", side_effect=controlled_now),
            patch.object(jobs, "_locked_context", side_effect=observed_locked_context),
        ):
            worker.start()
            self.assertTrue(verification_failed.wait(timeout=5))
            with transaction.atomic():
                jobs._locked_context(claimed.attempt.id)
                publication_phase.set()
                allow_failure.set()
                use_expired_time.set()
            self.assertTrue(lock_wait_started.wait(timeout=5))
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertTrue(failures.empty())
        completion = completions.get_nowait()
        claimed.attempt.refresh_from_db()
        receipt = ProcessingLateReceipt.objects.get(attempt=claimed.attempt)
        self.assertTrue(completion.stale)
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(receipt.payload["outcome"], "failure")
        self.assertEqual(receipt.payload["error_code"], "output_contract_violation")
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())
