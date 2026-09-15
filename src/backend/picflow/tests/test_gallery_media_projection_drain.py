from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from io import StringIO
from queue import Queue
from threading import Event as ThreadEvent
from threading import Thread
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase
from django.utils import timezone
from processing.contracts import AttemptCompletion
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services import jobs
from processing.services.enrollment import (
    GENERATE_PREVIEW_CONFIGURATION,
    request_processor,
)
from processing.services.jobs import claim_job, heartbeat_attempt, recover_expired_attempts
from processing.services.previews import complete_preview_attempt
from processing.storage import PreviewObject

from picflow.gallery_media_projection import (
    GalleryMediaPublicationDrainReport,
    drain_gallery_media_publications,
)
from picflow.models import Event, Photo


class _PreviewStorage:
    """Keep external bytes fake while exercising the real publication transactions."""

    def __init__(
        self,
        preview_object: PreviewObject,
        *,
        final_verified: ThreadEvent | None = None,
        release_final: ThreadEvent | None = None,
    ) -> None:
        self.preview_object = preview_object
        self.final_object: PreviewObject | None = None
        self.final_verified = final_verified
        self.release_final = release_final

    def verify(self, *, key: str, max_bytes: int) -> PreviewObject:  # noqa: ARG002
        if not key.startswith("derivatives/"):
            return self.preview_object
        if self.final_object is None:
            from ingestion.storage import ObjectMissing

            raise ObjectMissing()
        if self.final_verified is not None:
            self.final_verified.set()
            assert self.release_final is not None
            if not self.release_final.wait(timeout=5):
                raise AssertionError("test did not release storage-blocked completion")
        return self.final_object

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject:  # noqa: ARG002
        self.final_object = self.preview_object
        return self.preview_object


def _backend_is_waiting_for_lock(*, backend_pid: int, timeout_seconds: float = 5) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                [backend_pid],
            )
            row = cursor.fetchone()
        if row == ("Lock",):
            return True
        sleep(0.01)
    return False


class GalleryMediaPublicationDrainTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-drain-owner")
        self.event = Event.objects.create(
            name="Private gallery drain event",
            slug="private-gallery-drain-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def in_progress_attempt(self, suffix: str, *, processor_type: str) -> ProcessingAttempt:
        photo = Photo.objects.create(
            id=f"gallery-drain-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/private-{suffix}",
            original_filename=f"private-{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        configuration = {processor_type: {"variant": f"private-{suffix}"}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.PROCESSING,
            claimed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            claimed_at=timezone.now(),
            heartbeat_at=timezone.now(),
            lease_expires_at=timezone.now() + timedelta(minutes=5),
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=processor_type,
            status=PhotoProcessingState.Status.PROCESSING,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            processing_at=timezone.now(),
        )
        return attempt

    def claimed_preview(self, suffix: str):
        photo = Photo.objects.create(
            id=f"gdc-{hashlib.sha256(suffix.encode()).hexdigest()[:24]}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{uuid4().hex}",
            original_filename="private-preview.jpg",
            original_size=20,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        request_processor(
            photo,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
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
        claimed = claim_job(
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            worker_build="old-web-worker",
        )
        return photo, claimed

    @staticmethod
    def preview_object() -> PreviewObject:
        content = b"drain-test-preview"
        return PreviewObject(
            etag_wire='"drain-preview"',
            etag_value="drain-preview",
            byte_size=len(content),
            content_type="image/jpeg",
            sha256=hashlib.sha256(content).hexdigest(),
            width=1600,
            height=1000,
        )

    @staticmethod
    def preview_result(preview_object: PreviewObject) -> dict[str, object]:
        return {
            "variant": "preview-small-v1",
            "content_type": "image/jpeg",
            "byte_size": preview_object.byte_size,
            "width": preview_object.width,
            "height": preview_object.height,
            "oriented_source_width": 3200,
            "oriented_source_height": 2000,
            "sha256": preview_object.sha256,
            "upload_ms": 4,
            "warnings": [],
        }

    def test_command_requires_explicit_all_events_scope_before_locking(self) -> None:
        """The break caught here would allow an ambiguously scoped production drain."""
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "--all-events"):
            call_command("drain_gallery_media_publications", stdout=output)

        self.assertEqual(output.getvalue(), "")

    def test_command_fences_both_publication_processors_with_aggregate_output(self) -> None:
        """The break caught here would omit one publication kind or disclose row identity."""
        clean = self.in_progress_attempt("private-clean", processor_type=GENERATE_PREVIEW_PROCESSOR)
        watermark = self.in_progress_attempt(
            "private-watermark",
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )
        output = StringIO()

        call_command("drain_gallery_media_publications", "--all-events", stdout=output)

        clean.refresh_from_db()
        watermark.refresh_from_db()
        self.assertEqual(clean.lease_expires_at, clean.created_at)
        self.assertEqual(watermark.lease_expires_at, watermark.created_at)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"fenced_attempt_count": 2, "scope": "all_events", "status": "ok"},
        )
        rendered = output.getvalue()
        for private_value in (
            str(clean.pk),
            str(watermark.pk),
            clean.photo.original_key,
            watermark.photo.original_filename,
        ):
            self.assertNotIn(private_value, rendered)

    def test_expired_lease_publication_transaction_commits_before_drain_returns(self) -> None:
        """The break caught here would rebuild before an authorized old transaction commits."""
        photo, claimed = self.claimed_preview("publishing-expired")
        preview_object = self.preview_object()
        publication_clock = timezone.now()
        expiry = publication_clock + timedelta(milliseconds=200)
        ProcessingAttempt.objects.filter(pk=claimed.attempt.pk).update(lease_expires_at=expiry)
        publishing = ThreadEvent()
        release_publication = ThreadEvent()
        completion_results: Queue[AttemptCompletion] = Queue()
        completion_failures: Queue[BaseException] = Queue()
        drain_results: Queue[GalleryMediaPublicationDrainReport] = Queue()
        drain_failures: Queue[BaseException] = Queue()
        drain_pids: Queue[int] = Queue()
        original_terminal_success = jobs._terminal_success

        def pause_after_lease_check(*args, **kwargs):
            publishing.set()
            if not release_publication.wait(timeout=10):
                raise AssertionError("test did not release publishing transaction")
            return original_terminal_success(*args, **kwargs)

        def complete() -> None:
            close_old_connections()
            try:
                completion_results.put(
                    complete_preview_attempt(
                        claimed.attempt.pk,
                        result=self.preview_result(preview_object),
                        storage=_PreviewStorage(preview_object),
                        clock=lambda: publication_clock,
                    )
                )
            except BaseException as error:  # noqa: BLE001
                completion_failures.put(error)
            finally:
                close_old_connections()

        def drain() -> None:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    drain_pids.put(cursor.fetchone()[0])
                drain_results.put(drain_gallery_media_publications())
            except BaseException as error:  # noqa: BLE001
                drain_failures.put(error)
            finally:
                close_old_connections()

        with patch.object(jobs, "_terminal_success", side_effect=pause_after_lease_check):
            completion_thread = Thread(target=complete)
            completion_thread.start()
            self.assertTrue(publishing.wait(timeout=5))
            expiry_deadline = monotonic() + 2
            while timezone.now() <= expiry and monotonic() < expiry_deadline:
                sleep(0.01)
            self.assertGreater(timezone.now(), expiry)
            drain_thread = Thread(target=drain)
            drain_thread.start()
            drain_pid = drain_pids.get(timeout=5)
            drain_waited = _backend_is_waiting_for_lock(backend_pid=drain_pid)
            drain_returned_before_release = not drain_results.empty()
            release_publication.set()
            completion_thread.join(timeout=5)
            drain_thread.join(timeout=5)

        self.assertTrue(drain_waited)
        self.assertFalse(drain_returned_before_release)
        self.assertFalse(completion_thread.is_alive())
        self.assertFalse(drain_thread.is_alive())
        self.assertTrue(completion_failures.empty(), list(completion_failures.queue))
        self.assertTrue(drain_failures.empty(), list(drain_failures.queue))
        self.assertFalse(completion_results.get_nowait().stale)
        self.assertEqual(drain_results.get_nowait().fenced_attempt_count, 0)
        self.assertTrue(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_storage_blocked_completion_cannot_publish_after_drain(self) -> None:
        """The break caught here would let verified old storage work publish after the snapshot."""
        photo, claimed = self.claimed_preview("storage-blocked")
        preview_object = self.preview_object()
        final_verified = ThreadEvent()
        release_final = ThreadEvent()
        results: Queue[AttemptCompletion] = Queue()
        failures: Queue[BaseException] = Queue()

        def complete() -> None:
            close_old_connections()
            try:
                results.put(
                    complete_preview_attempt(
                        claimed.attempt.pk,
                        result=self.preview_result(preview_object),
                        storage=_PreviewStorage(
                            preview_object,
                            final_verified=final_verified,
                            release_final=release_final,
                        ),
                    )
                )
            except BaseException as error:  # noqa: BLE001
                failures.put(error)
            finally:
                close_old_connections()

        completion_thread = Thread(target=complete)
        completion_thread.start()
        self.assertTrue(final_verified.wait(timeout=5))
        report = drain_gallery_media_publications()
        release_final.set()
        completion_thread.join(timeout=5)

        self.assertEqual(report.fenced_attempt_count, 1)
        self.assertFalse(completion_thread.is_alive())
        self.assertTrue(failures.empty(), list(failures.queue))
        self.assertTrue(results.get_nowait().stale)
        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertTrue(ProcessingLateReceipt.objects.filter(attempt=claimed.attempt).exists())
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())

    def test_pre_drain_heartbeat_waiting_on_fence_cannot_renew_lease(self) -> None:
        """The break caught here would let an accepted old heartbeat resurrect publication."""
        _photo, claimed = self.claimed_preview("heartbeat-waiting")
        heartbeat_now = timezone.now()
        self.assertGreaterEqual(heartbeat_now, claimed.attempt.created_at)
        heartbeat_before_lock = ThreadEvent()
        allow_heartbeat_lock = ThreadEvent()
        fence_applied = ThreadEvent()
        release_fence = ThreadEvent()
        drain_failures: Queue[BaseException] = Queue()
        heartbeat_results: Queue[ProcessingAttempt | None] = Queue()
        heartbeat_failures: Queue[BaseException] = Queue()
        heartbeat_pids: Queue[int] = Queue()
        original_update = QuerySet.update
        original_locked_context = jobs._locked_context

        def hold_heartbeat_before_lock(attempt_id):
            heartbeat_before_lock.set()
            if not allow_heartbeat_lock.wait(timeout=10):
                raise AssertionError("test did not allow pre-drain heartbeat to acquire locks")
            return original_locked_context(attempt_id)

        def hold_fence(queryset, **kwargs):
            result = original_update(queryset, **kwargs)
            if queryset.model is ProcessingAttempt and "lease_expires_at" in kwargs:
                fence_applied.set()
                if not release_fence.wait(timeout=10):
                    raise AssertionError("test did not release drain transaction")
            return result

        def drain() -> None:
            close_old_connections()
            try:
                drain_gallery_media_publications()
            except BaseException as error:  # noqa: BLE001
                drain_failures.put(error)
            finally:
                close_old_connections()

        def heartbeat() -> None:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    heartbeat_pids.put(cursor.fetchone()[0])
                heartbeat_results.put(
                    heartbeat_attempt(
                        claimed.attempt.pk,
                        lease_seconds=120,
                    )
                )
            except BaseException as error:  # noqa: BLE001
                heartbeat_failures.put(error)
            finally:
                close_old_connections()

        with (
            patch.object(QuerySet, "update", new=hold_fence),
            patch.object(jobs, "_locked_context", side_effect=hold_heartbeat_before_lock),
            patch("processing.services.jobs.timezone.now", return_value=heartbeat_now),
        ):
            heartbeat_thread = Thread(target=heartbeat)
            heartbeat_thread.start()
            heartbeat_pid = heartbeat_pids.get(timeout=5)
            self.assertTrue(heartbeat_before_lock.wait(timeout=5))
            drain_thread = Thread(target=drain)
            drain_thread.start()
            self.assertTrue(fence_applied.wait(timeout=5))
            allow_heartbeat_lock.set()
            heartbeat_waited = _backend_is_waiting_for_lock(backend_pid=heartbeat_pid)
            release_fence.set()
            drain_thread.join(timeout=5)
            heartbeat_thread.join(timeout=5)

        self.assertTrue(heartbeat_waited)
        self.assertFalse(drain_thread.is_alive())
        self.assertFalse(heartbeat_thread.is_alive())
        self.assertTrue(drain_failures.empty(), list(drain_failures.queue))
        self.assertTrue(heartbeat_failures.empty(), list(heartbeat_failures.queue))
        self.assertIsNone(heartbeat_results.get_nowait())
        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.lease_expires_at, claimed.attempt.created_at)

    def test_lock_timeout_is_sanitized_and_preserves_all_leases(self) -> None:
        """The break caught here would leave worker publication paused on a stuck DB lock."""
        first = self.in_progress_attempt("timeout-first", processor_type=GENERATE_PREVIEW_PROCESSOR)
        locked = self.in_progress_attempt(
            "timeout-locked", processor_type=GENERATE_PREVIEW_PROCESSOR
        )
        original_expiries = {
            first.pk: first.lease_expires_at,
            locked.pk: locked.lease_expires_at,
        }
        lock_acquired = ThreadEvent()
        release_lock = ThreadEvent()
        lock_failures: Queue[BaseException] = Queue()

        def hold_attempt_lock() -> None:
            close_old_connections()
            try:
                with transaction.atomic():
                    ProcessingAttempt.objects.select_for_update().get(pk=locked.pk)
                    lock_acquired.set()
                    release_lock.wait(timeout=2)
            except BaseException as error:  # noqa: BLE001
                lock_failures.put(error)
            finally:
                close_old_connections()

        holder = Thread(target=hold_attempt_lock)
        holder.start()
        self.assertTrue(lock_acquired.wait(timeout=5))
        output = StringIO()
        try:
            with (
                patch(
                    "picflow.gallery_media_projection.PUBLICATION_DRAIN_LOCK_TIMEOUT_MS",
                    100,
                    create=True,
                ),
                patch(
                    "picflow.gallery_media_projection.PUBLICATION_DRAIN_STATEMENT_TIMEOUT_MS",
                    500,
                    create=True,
                ),
            ):
                with self.assertRaisesRegex(CommandError, "publication drain failed") as error:
                    call_command(
                        "drain_gallery_media_publications",
                        "--all-events",
                        stdout=output,
                    )
        finally:
            release_lock.set()
            holder.join(timeout=5)

        self.assertFalse(holder.is_alive())
        self.assertTrue(lock_failures.empty(), list(lock_failures.queue))
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(str(locked.pk), str(error.exception))
        actual_expiries = dict(
            ProcessingAttempt.objects.filter(pk__in=original_expiries).values_list(
                "pk", "lease_expires_at"
            )
        )
        self.assertEqual(actual_expiries, original_expiries)

    def test_statement_timeout_rolls_back_fences_and_is_sanitized(self) -> None:
        """The break caught here would commit partial fences after the bounded command fails."""
        attempt = self.in_progress_attempt(
            "statement-timeout",
            processor_type=GENERATE_PREVIEW_PROCESSOR,
        )
        original_expiry = attempt.lease_expires_at
        original_update = QuerySet.update

        def update_then_stall(queryset, **kwargs):
            result = original_update(queryset, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(0.2)")
            return result

        output = StringIO()
        with (
            patch.object(QuerySet, "update", new=update_then_stall),
            patch(
                "picflow.gallery_media_projection.PUBLICATION_DRAIN_LOCK_TIMEOUT_MS",
                100,
                create=True,
            ),
            patch(
                "picflow.gallery_media_projection.PUBLICATION_DRAIN_STATEMENT_TIMEOUT_MS",
                100,
                create=True,
            ),
        ):
            with self.assertRaisesRegex(CommandError, "publication drain failed") as error:
                call_command(
                    "drain_gallery_media_publications",
                    "--all-events",
                    stdout=output,
                )

        attempt.refresh_from_db()
        self.assertEqual(attempt.lease_expires_at, original_expiry)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(str(attempt.pk), str(error.exception))

    def test_fenced_attempt_uses_normal_expired_lease_retry_after_workers_resume(self) -> None:
        """The break caught here would strand a drained attempt outside normal retry recovery."""
        attempt = self.in_progress_attempt(
            "normal-retry", processor_type=GENERATE_PREVIEW_PROCESSOR
        )

        drain_gallery_media_publications()
        recovered = recover_expired_attempts(now=timezone.now(), jitter=lambda _low, _high: 0)

        self.assertEqual([item.pk for item in recovered], [attempt.pk])
        attempt.refresh_from_db()
        attempt.job.refresh_from_db()
        state = PhotoProcessingState.objects.get(
            photo=attempt.photo,
            processor_type=attempt.processor_type,
        )
        self.assertEqual(attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(attempt.job.status, ProcessingJob.Status.RETRY_WAIT)
        self.assertEqual(state.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertIsNone(state.current_attempt_id)
