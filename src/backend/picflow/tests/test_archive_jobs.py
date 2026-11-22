from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from ingestion.storage import ObjectMissing, OpenedObject, StorageUnavailable

from picflow.archive import ArchiveEntry, ArchiveObservation
from picflow.archive_jobs import enqueue_archive_job, find_archive_job, sign_archive_job
from picflow.models import PhotoArchiveJob

_ENTRY = ArchiveEntry("first", "originals/" + "a" * 32, 3, "image/jpeg")
_SECOND = ArchiveEntry("second", "originals/" + "b" * 32, 3, "image/png")


class ArchiveJobTests(TestCase):
    def _job(
        self,
        entries: Sequence[ArchiveEntry] = (_ENTRY,),
        *,
        scope: str = "search:2",
        page: int = 1,
    ) -> PhotoArchiveJob:
        return enqueue_archive_job(
            scope=scope,
            entries=entries,
            filename="findme-photo-гонка-search-results.zip",
            observation=ArchiveObservation(context="free_result", page=page),
        )

    def test_identity_and_read_only_lookup_use_exact_page_metadata(self) -> None:
        assert find_archive_job(uuid4()) is None
        assert not PhotoArchiveJob.objects.exists()
        first = self._job()
        assert find_archive_job(first.public_id) == first
        assert find_archive_job(first.object_id) is None
        assert self._job().public_id == first.public_id
        assert self._job((replace(_ENTRY, original_key=_SECOND.original_key),)) != first
        assert self._job((replace(_ENTRY, original_size=4),)) != first
        assert self._job((replace(_ENTRY, original_content_type="image/png"),)) != first
        assert self._job(scope="search:3") != first
        assert self._job(page=2) != first

    @patch("picflow.archive_jobs.PrivateUploadStorage")
    def test_failed_source_does_not_upload_and_explicit_retry_builds_complete_zip(
        self, storage_class
    ) -> None:
        job = self._job((_ENTRY, _SECOND))
        storage = storage_class.return_value
        storage.open_final.side_effect = [
            OpenedObject(BytesIO(b"one"), 3, "image/jpeg"),
            ObjectMissing(),
        ]

        with (
            patch("config.queue_work.notify_queue_result") as notify,
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command("run_archive_worker", once=True)
        notify.assert_called_once_with("photo_archive", "failed", None, count=1)
        job.refresh_from_db()
        assert job.status == PhotoArchiveJob.Status.FAILED
        storage.upload_archive.assert_not_called()
        with self.assertRaises(StorageUnavailable):
            sign_archive_job(job)
        status_job = find_archive_job(job.public_id)
        assert status_job is not None
        assert status_job.status == PhotoArchiveJob.Status.FAILED

        retried = self._job((_ENTRY, _SECOND))
        assert retried.pk == job.pk
        assert find_archive_job(job.public_id) is None
        assert retried.public_id != job.public_id
        assert retried.status == PhotoArchiveJob.Status.QUEUED
        storage.open_final.side_effect = [
            OpenedObject(BytesIO(b"one"), 3, "image/jpeg"),
            OpenedObject(BytesIO(b"two"), 3, "image/png"),
        ]

        def capture_archive(*, object_id, body, filename) -> None:
            assert object_id != job.object_id
            assert filename == job.filename
            with ZipFile(body) as archive:
                assert archive.namelist() == [
                    "findme-photo-first.jpg",
                    "findme-photo-second.png",
                ]
                assert archive.read("findme-photo-first.jpg") == b"one"
                assert archive.read("findme-photo-second.png") == b"two"

        storage.upload_archive.side_effect = capture_archive
        call_command("run_archive_worker", once=True)
        retried.refresh_from_db()
        assert retried.status == PhotoArchiveJob.Status.READY
        assert retried.expires_at is not None and retried.expires_at > timezone.now()
        with patch("picflow.archive_jobs.sign_private_archive") as sign:
            sign.return_value = "https://img.findme-photo.ru/current"
            assert sign_archive_job(retried) == "https://img.findme-photo.ru/current"
        assert sign.call_args.kwargs == {"object_id": retried.object_id, "expires_in": 300}
        assert self._job((_ENTRY, _SECOND)).public_id == retried.public_id

    @patch("picflow.archive_jobs.PrivateUploadStorage")
    def test_restarts_interrupted_job_and_rotates_artifact_id(self, storage_class) -> None:
        job = self._job()
        job.status = PhotoArchiveJob.Status.RUNNING
        job.save(update_fields=["status"])
        old_object_id = job.object_id
        storage_class.return_value.open_final.return_value = OpenedObject(
            BytesIO(b"one"), 3, "image/jpeg"
        )

        call_command("run_archive_worker", once=True)
        job.refresh_from_db()
        assert job.status == PhotoArchiveJob.Status.READY
        assert job.object_id != old_object_id

    def test_expired_job_cannot_be_signed_and_is_pruned(self) -> None:
        job = self._job()
        job.status = PhotoArchiveJob.Status.READY
        job.expires_at = timezone.now() - timedelta(seconds=1)
        job.save(update_fields=["status", "expires_at"])
        with self.assertRaises(StorageUnavailable):
            sign_archive_job(job)
        assert find_archive_job(job.public_id) is None

        call_command("run_archive_worker", once=True)
        assert not PhotoArchiveJob.objects.filter(pk=job.pk).exists()

    def test_expired_capability_cannot_access_replacement_job(self) -> None:
        job = self._job()
        job.status = PhotoArchiveJob.Status.READY
        job.expires_at = timezone.now() - timedelta(seconds=1)
        job.save(update_fields=["status", "expires_at"])

        replacement = self._job()
        assert replacement.pk == job.pk
        assert replacement.public_id != job.public_id
        assert find_archive_job(replacement.public_id) == replacement
        assert find_archive_job(job.public_id) is None

    @override_settings(
        GALLERY_CDN_ORIGIN="https://img.findme-photo.ru",
        GALLERY_CDN_TOKEN_SECRET="test-secret",
        OPERATIONAL_S3_BUCKET="test-operational",
        PRIVATE_MEDIA_S3_ENDPOINT_URL="malformed-archive-storage-endpoint",
        PRIVATE_MEDIA_S3_ACCESS_KEY_ID="test",
        PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY="test",
    )
    def test_invalid_storage_endpoint_fails_job_and_signing_cleanly(self) -> None:
        job = self._job()
        call_command("run_archive_worker", once=True)
        job.refresh_from_db()
        assert job.status == PhotoArchiveJob.Status.FAILED

        job.status = PhotoArchiveJob.Status.READY
        job.expires_at = timezone.now() + timedelta(hours=1)
        with self.assertRaisesMessage(StorageUnavailable, "Object storage is unavailable."):
            sign_archive_job(job)

    @patch("picflow.archive_jobs.PrivateUploadStorage")
    def test_success_reports_queue_segments_only_after_commit(self, storage_class) -> None:
        job = self._job()
        now = timezone.now()
        PhotoArchiveJob.objects.filter(pk=job.pk).update(
            queue_enqueued_at=now - timedelta(seconds=4)
        )
        storage_class.return_value.open_final.return_value = OpenedObject(
            BytesIO(b"one"), 3, "image/jpeg"
        )

        with (
            patch("config.queue_work.notify_queue_result") as notify,
            patch("picflow.archive_jobs.timezone.now", return_value=now),
            patch(
                "picflow.archive_jobs.monotonic_ns",
                side_effect=[0, 1_000_000_000, 3_000_000_000, 6_000_000_000],
            ),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("run_archive_worker", once=True)
                job.refresh_from_db()
                assert job.status == PhotoArchiveJob.Status.READY
                notify.assert_not_called()

            notify.assert_called_once_with(
                "photo_archive",
                "success",
                {"wait": 4000, "retry": 0, "capacity": 1000, "build": 2000, "upload": 3000},
                count=1,
            )

    def test_shared_queue_metrics_observe_only_claimable_work_and_latest_success(self) -> None:
        from config.queue_metrics import collect_queue_samples

        now = timezone.now()
        oldest = self._job(scope="waiting:1")
        self._job(scope="waiting:2")
        running = self._job(scope="running")
        failed = self._job(scope="failed")
        ready = self._job(scope="ready")
        PhotoArchiveJob.objects.filter(pk=oldest.pk).update(updated_at=now - timedelta(seconds=20))
        PhotoArchiveJob.objects.filter(pk=running.pk).update(status=PhotoArchiveJob.Status.RUNNING)
        PhotoArchiveJob.objects.filter(pk=failed.pk).update(status=PhotoArchiveJob.Status.FAILED)
        PhotoArchiveJob.objects.filter(pk=ready.pk).update(
            status=PhotoArchiveJob.Status.READY, updated_at=now
        )

        count, age, succeeded_at, observed_at = collect_queue_samples()["photo_archive"]
        assert count == 2
        assert age >= 20
        assert succeeded_at == now.timestamp()
        assert observed_at >= succeeded_at

        PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.QUEUED).delete()
        assert collect_queue_samples()["photo_archive"][:2] == (0, 0.0)


class ConcurrentArchiveJobTests(TransactionTestCase):
    def test_two_slots_claim_distinct_jobs_and_finish_them(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from django.db import connections

        from picflow.archive_jobs import process_next_archive

        for index in range(2):
            enqueue_archive_job(
                scope=f"parallel:{index}",
                entries=(_ENTRY,),
                filename="findme-photo-test-search-results.zip",
                observation=ArchiveObservation(context="free_result", page=1),
            )
        barrier = Barrier(2)
        claimed = []

        def build(job):
            claimed.append(job.pk)
            barrier.wait(timeout=5)
            return 0

        def run():
            try:
                return process_next_archive()
            finally:
                connections.close_all()

        with patch("picflow.archive_jobs._build_and_upload", side_effect=build):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run) for _ in range(2)]
                assert all(f.result(timeout=10) for f in futures)
        assert len(set(claimed)) == 2
        assert PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.READY).count() == 2


class ArchiveWorkerLifecycleTests(TransactionTestCase):
    def test_prunes_before_claims_and_holds_lock_until_active_slots_finish(self):
        from threading import Barrier

        from picflow.management.commands import run_archive_worker as worker

        events: list[str] = []
        barrier = Barrier(2)
        handlers = []

        def install_handler(signum, handler):
            handlers.append(handler)
            return None

        def execute(sql, params):
            if "pg_advisory_unlock" in sql:
                assert events.count("finished") == 2
                events.append("unlocked")

        def prune():
            events.append("pruned")

        def process():
            assert "pruned" in events
            events.append("claimed")
            barrier.wait(timeout=5)
            handlers[0](None, None)
            events.append("finished")
            return True

        with (
            patch.object(worker.connection, "cursor") as cursor,
            patch.object(worker.signal, "signal", side_effect=install_handler),
            patch.object(worker, "recover_interrupted_archives") as recover,
            patch.object(worker, "prune_archive_jobs", side_effect=prune),
            patch.object(worker, "process_next_archive", side_effect=process),
        ):
            cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)
            cursor.return_value.__enter__.return_value.execute.side_effect = execute
            worker.Command().handle(once=False)

        recover.assert_called_once_with()
        assert events[0] == "pruned"
        assert events.count("pruned") == 1
        assert events.count("claimed") == 2
        assert events[-1] == "unlocked"


class ArchiveSpaceTests(TransactionTestCase):
    def test_reservations_serialize_large_archives_and_release_on_failure(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from types import SimpleNamespace

        from picflow import archive_jobs

        first_active = Event()
        release_first = Event()
        second_active = Event()
        entries = [{"original_size": 500_000}]

        def first():
            with archive_jobs._reserve_archive_space(entries):
                first_active.set()
                assert release_first.wait(timeout=5)

        def second():
            with archive_jobs._reserve_archive_space(entries):
                second_active.set()
                raise OSError("upload failed")

        with (
            patch.object(archive_jobs, "disk_usage", return_value=SimpleNamespace(free=1_000_000)),
            patch.object(archive_jobs, "_DISK_MARGIN_BYTES", 0),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(first)
                assert first_active.wait(timeout=5)
                b = pool.submit(second)
                assert not second_active.wait(timeout=0.05)
                release_first.set()
                a.result(timeout=5)
                with self.assertRaisesMessage(OSError, "upload failed"):
                    b.result(timeout=5)
        assert archive_jobs._reserved_archive_bytes == 0

    def test_insufficient_space_without_active_reservation_fails_immediately(self):
        from types import SimpleNamespace

        from picflow import archive_jobs

        with patch.object(archive_jobs, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesMessage(OSError, "insufficient temporary archive space"):
                with archive_jobs._reserve_archive_space([{"original_size": 10}]):
                    self.fail("reservation unexpectedly granted")
        assert archive_jobs._reserved_archive_bytes == 0


class ArchiveWorkerIdleTests(TransactionTestCase):
    def test_idle_slot_waits_without_sql_rechecks_and_stops(self):
        from threading import Event

        from picflow.management.commands import run_archive_worker as worker

        stopping = Event()
        waits = []

        def wait(listener, *, timeout):
            waits.append(timeout)
            if len(waits) == 3:
                stopping.set()
            return []

        with (
            patch.object(worker, "process_next_archive", return_value=False) as process,
            patch.object(worker, "wait_for_notifications", side_effect=wait, create=True),
            patch.object(worker.connections, "close_all") as close,
        ):
            worker._run_slot(stopping)
        assert len(waits) == 3
        process.assert_called_once_with()
        close.assert_called_once_with()


class ArchiveWakeupTests(TransactionTestCase):
    _job = ArchiveJobTests._job

    def test_enqueue_retry_and_recovery_notify_only_on_commit(self):
        import psycopg
        from django.db import connection, transaction

        with psycopg.connect(**connection.get_connection_params(), autocommit=True) as listener:
            listener.execute("LISTEN archive_jobs")
            with transaction.atomic():
                job = self._job()
                assert list(listener.notifies(timeout=0.02)) == []
            assert len(list(listener.notifies(timeout=0.05))) == 1
            with self.assertRaises(ValueError), transaction.atomic():
                self._job(scope="rolled-back")
                raise ValueError()
            assert list(listener.notifies(timeout=0.02)) == []
            PhotoArchiveJob.objects.filter(pk=job.pk).update(status="failed")
            with self.assertRaises(ValueError), transaction.atomic():
                self._job()
                raise ValueError()
            assert list(listener.notifies(timeout=0.02)) == []
            self._job()
            assert len(list(listener.notifies(timeout=0.05))) == 1
            PhotoArchiveJob.objects.filter(pk=job.pk).update(status="running")
            from picflow.archive_jobs import recover_interrupted_archives

            recover_interrupted_archives()
            assert len(list(listener.notifies(timeout=0.05))) == 1


class ArchiveLockWakeupTests(TransactionTestCase):
    _job = ArchiveJobTests._job

    def test_queued_job_locked_by_rolled_back_duplicate_is_retried_without_new_notify(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from django.db import connections, transaction

        from picflow.archive_jobs import process_next_archive
        from picflow.management.commands import run_archive_worker as worker

        job = self._job()
        locked, release, waiting, processed, stopping = (Event() for _ in range(5))

        def duplicate():
            try:
                with transaction.atomic():
                    self._job()  # Existing production duplicate enqueue takes this row lock.
                    locked.set()
                    assert release.wait(5)
                    raise ValueError("audit rollback")
            except ValueError:
                pass
            finally:
                connections.close_all()

        def scan():
            result = process_next_archive()
            if result:
                processed.set()
                stopping.set()
            return result

        def wait(listener, *, timeout):
            waiting.set()
            from config.queue_notifications import wait_for_notifications

            return wait_for_notifications(listener, timeout=timeout)

        with (
            patch.object(worker, "process_next_archive", side_effect=scan),
            patch.object(worker, "wait_for_notifications", side_effect=wait),
            patch("picflow.archive_jobs._build_and_upload", return_value=0),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            duplicate_future = pool.submit(duplicate)
            slot = None
            try:
                assert locked.wait(5)
                slot = pool.submit(worker._run_slot, stopping)
                assert waiting.wait(5)
                release.set()
                duplicate_future.result(timeout=5)
                assert processed.wait(2), "rolled-back duplicate left committed queued work asleep"
            finally:
                release.set()
                stopping.set()
                if slot is not None:
                    slot.result(timeout=5)
        job.refresh_from_db()
        assert job.status == PhotoArchiveJob.Status.READY
