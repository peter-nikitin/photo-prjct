from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.core.management import call_command
from django.test import TestCase, override_settings
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

        call_command("run_archive_worker", once=True)
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
