from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from feature_flags.registry import YANDEX_DISK_IMPORT
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.models import ImportAttempt, ImportBatch, ImportItem
from ingestion.services.imports import (
    ImportConflict,
    ManifestEntry,
    claim_import_work,
    create_import,
    finish_manifest,
    record_import_failure,
    record_manifest_page,
    renew_import_lease,
    retry_import_errors,
)
from picflow.models import Event, EventFolder


class ImportServiceTests(TestCase):
    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="import-owner")
        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        self.owner.user_permissions.add(permission)
        self.event = Event.objects.create(
            name="Import services",
            slug="import-services",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.folder = EventFolder.objects.create(event=self.event, name="Start")
        self.flags = {YANDEX_DISK_IMPORT: FEATURE_FLAG_ON}

    def create_and_claim_manifest(self, *, submission_key: str = "submission"):
        with override_feature_flags(self.flags):
            created = create_import(
                actor=self.owner,
                event=self.event,
                folder=self.folder,
                submitted_source_key="submitted-public-key",
                submission_key=submission_key,
            )
            claimed = claim_import_work(lease_seconds=60)
        self.assertEqual(claimed.kind, "manifest")
        assert claimed.attempt_id is not None
        assert claimed.lease_expires_at is not None
        return created, claimed

    def finish_with_entries(self, entries: tuple[ManifestEntry, ...]):
        created, claimed = self.create_and_claim_manifest()
        with override_feature_flags(self.flags):
            record_manifest_page(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                page_number=0,
                page_fingerprint="page-zero",
                entries=entries,
            )
            result = finish_manifest(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                canonical_source_key="canonical-public-folder",
            )
        return created, result

    def test_create_is_idempotent_and_gate_and_permission_are_current(self) -> None:
        with override_feature_flags(self.flags):
            first = create_import(
                actor=self.owner,
                event=self.event,
                folder=self.folder,
                submitted_source_key="submitted-public-key",
                submission_key="stable-request",
            )
            second = create_import(
                actor=self.owner,
                event=self.event,
                folder=self.folder,
                submitted_source_key="submitted-public-key",
                submission_key="stable-request",
            )
        self.assertEqual(first.id, second.id)
        self.flags[YANDEX_DISK_IMPORT] = FEATURE_FLAG_OFF
        with override_feature_flags(self.flags), self.assertRaises(ImportConflict) as raised:
            create_import(
                actor=self.owner,
                event=self.event,
                folder=self.folder,
                submitted_source_key="other",
                submission_key="blocked-request",
            )
        self.assertEqual(raised.exception.code, "feature_paused")

    def test_manifest_pages_are_replay_safe_and_finish_before_file_claims(self) -> None:
        created, claimed = self.create_and_claim_manifest()
        entries = (
            ManifestEntry(
                path="/photo.jpg",
                name="photo.jpg",
                kind="jpeg",
                size=321,
                sha256="a" * 64,
                md5="b" * 32,
                version="v1",
            ),
            ManifestEntry(path="/subfolder", name="subfolder", kind="directory"),
            ManifestEntry(path="/notes.txt", name="notes.txt", kind="unsupported", size=12),
        )
        with override_feature_flags(self.flags):
            first = record_manifest_page(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                page_number=0,
                page_fingerprint="same-page",
                entries=entries,
            )
            replay = record_manifest_page(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                page_number=0,
                page_fingerprint="same-page",
                entries=entries,
            )
            completed = finish_manifest(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                canonical_source_key="canonical-public-folder",
            )
            completed_replay = finish_manifest(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                canonical_source_key="canonical-public-folder",
            )
            file_claim = claim_import_work(lease_seconds=60)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(ImportItem.objects.filter(batch_id=created.id).count(), 1)
        self.assertEqual(completed.jpeg_count, 1)
        self.assertEqual(completed.directory_count, 1)
        self.assertEqual(completed.unsupported_count, 1)
        self.assertEqual(completed_replay, completed)
        self.assertEqual(file_claim.kind, "file")
        self.assertEqual(file_claim.item_id, ImportItem.objects.get(batch_id=created.id).id)

    def test_manifest_replay_rejects_changed_entries_even_with_same_page_identity(self) -> None:
        created, claimed = self.create_and_claim_manifest()
        with override_feature_flags(self.flags):
            record_manifest_page(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                page_number=0,
                page_fingerprint="stable-source-page",
                entries=(ManifestEntry(path="/one.jpg", name="one.jpg", kind="jpeg", size=10),),
            )
            with self.assertRaises(ImportConflict) as raised:
                record_manifest_page(
                    batch_id=created.id,
                    attempt_id=claimed.attempt_id,
                    page_number=0,
                    page_fingerprint="stable-source-page",
                    entries=(ManifestEntry(path="/two.jpg", name="two.jpg", kind="jpeg", size=10),),
                )
        self.assertEqual(raised.exception.code, "manifest_changed")

    def test_empty_manifest_completes_without_a_photo(self) -> None:
        created, result = self.finish_with_entries(
            (ManifestEntry(path="/folder", name="folder", kind="directory"),)
        )

        self.assertEqual(result.status, ImportBatch.Status.COMPLETED)
        self.assertEqual(result.jpeg_count, 0)
        self.assertEqual(result.directory_count, 1)
        self.assertEqual(ImportItem.objects.filter(batch_id=created.id).count(), 0)

    def test_oversized_jpeg_is_an_item_error_and_valid_neighbor_is_claimed(self) -> None:
        created, result = self.finish_with_entries(
            (
                ManifestEntry(
                    path="/oversized.jpg",
                    name="oversized.jpg",
                    kind="jpeg",
                    size=52_428_801,
                ),
                ManifestEntry(path="/valid.jpg", name="valid.jpg", kind="jpeg", size=100),
            )
        )

        oversized = ImportItem.objects.get(batch_id=created.id, source_path="/oversized.jpg")
        self.assertEqual(oversized.status, ImportItem.Status.ERROR)
        self.assertEqual(oversized.error_code, "file_too_large")
        self.assertIsNotNone(oversized.completed_at)
        self.assertEqual(result.jpeg_count, 2)
        self.assertEqual(result.error_count, 1)
        with override_feature_flags(self.flags):
            claimed = claim_import_work()
        self.assertEqual(claimed.item_id, ImportItem.objects.get(source_path="/valid.jpg").id)

    def test_expired_lease_is_reclaimed_and_stale_attempt_cannot_mutate(self) -> None:
        created, claimed = self.create_and_claim_manifest()
        with override_feature_flags(self.flags):
            record_manifest_page(
                batch_id=created.id,
                attempt_id=claimed.attempt_id,
                page_number=0,
                page_fingerprint="abandoned-page",
                entries=(
                    ManifestEntry(
                        path="/abandoned.jpg",
                        name="abandoned.jpg",
                        kind="jpeg",
                        size=10,
                    ),
                ),
            )
        after_expiry = claimed.lease_expires_at + timedelta(seconds=1)
        with override_feature_flags(self.flags):
            replacement = claim_import_work(lease_seconds=60, now=after_expiry)
        self.assertNotEqual(replacement.attempt_id, claimed.attempt_id)
        self.assertFalse(ImportItem.objects.filter(batch_id=created.id).exists())
        with override_feature_flags(self.flags), self.assertRaises(ImportConflict) as raised:
            renew_import_lease(claimed.attempt_id, lease_seconds=60, now=after_expiry)
        self.assertEqual(raised.exception.code, "stale_attempt")
        self.assertEqual(
            ImportAttempt.objects.get(pk=claimed.attempt_id).status,
            ImportAttempt.Status.EXPIRED,
        )

    def test_gate_pause_and_fresh_permission_resume_without_retry_penalty(self) -> None:
        created, claimed = self.create_and_claim_manifest()
        now = claimed.lease_expires_at + timedelta(seconds=1)
        self.flags[YANDEX_DISK_IMPORT] = FEATURE_FLAG_OFF
        with override_feature_flags(self.flags):
            self.assertEqual(claim_import_work(now=now).kind, "empty")
        created_batch = ImportBatch.objects.get(pk=created.id)
        self.assertEqual(created_batch.status, ImportBatch.Status.PAUSED)
        self.assertEqual(created_batch.manifest_attempts, 1)

        self.flags[YANDEX_DISK_IMPORT] = FEATURE_FLAG_ON
        with override_feature_flags(self.flags):
            resumed = claim_import_work(now=now + timedelta(seconds=1))
        self.assertEqual(resumed.kind, "manifest")
        assert resumed.lease_expires_at is not None
        created_batch.refresh_from_db()
        self.assertEqual(created_batch.manifest_attempts, 2)

        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        self.owner.user_permissions.remove(permission)
        revoked_at = resumed.lease_expires_at + timedelta(seconds=1)
        with override_feature_flags(self.flags):
            self.assertEqual(claim_import_work(now=revoked_at).kind, "empty")
        created_batch.refresh_from_db()
        self.assertEqual(created_batch.status, ImportBatch.Status.PAUSED)
        self.assertEqual(created_batch.manifest_attempts, 2)

    def test_each_source_operation_is_bounded_to_four_attempts(self) -> None:
        created, claimed = self.create_and_claim_manifest()
        current = claimed
        with override_feature_flags(self.flags):
            for attempt_number in range(1, 5):
                result = record_import_failure(
                    attempt_id=current.attempt_id,
                    operation="manifest",
                    code="source_unavailable",
                    retryable=True,
                )
                self.assertEqual(result.manifest_attempts, attempt_number)
                replay = record_import_failure(
                    attempt_id=current.attempt_id,
                    operation="manifest",
                    code="source_unavailable",
                    retryable=True,
                )
                self.assertEqual(replay, result)
                if attempt_number < 4:
                    current = claim_import_work(lease_seconds=60)
        batch = ImportBatch.objects.get(pk=created.id)
        self.assertEqual(batch.status, ImportBatch.Status.FAILED)
        with override_feature_flags(self.flags):
            self.assertEqual(claim_import_work().kind, "empty")

    def test_retry_errors_only_resets_failed_manifest_or_items(self) -> None:
        created, result = self.finish_with_entries(
            (
                ManifestEntry(path="/one.jpg", name="one.jpg", kind="jpeg", size=10),
                ManifestEntry(path="/two.jpg", name="two.jpg", kind="jpeg", size=11),
            )
        )
        self.assertEqual(result.jpeg_count, 2)
        first, second = ImportItem.objects.filter(batch_id=created.id).order_by("source_path")
        first.status = ImportItem.Status.IMPORTED
        first.save(update_fields=["status"])
        second.status = ImportItem.Status.ERROR
        second.download_attempts = 4
        second.error_code = "source_unavailable"
        second.save(update_fields=["status", "download_attempts", "error_code"])
        ImportBatch.objects.filter(pk=created.id).update(status=ImportBatch.Status.PARTIAL)

        with override_feature_flags(self.flags):
            retried = retry_import_errors(actor=self.owner, batch_id=created.id)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, ImportItem.Status.IMPORTED)
        self.assertEqual(second.status, ImportItem.Status.PENDING)
        self.assertEqual(second.download_attempts, 0)
        self.assertEqual(retried.status, ImportBatch.Status.TRANSFERRING)
