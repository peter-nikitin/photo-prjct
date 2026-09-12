from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from feature_flags.registry import YANDEX_DISK_IMPORT
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.models import ImportAttempt, ImportBatch, ImportedContent, ImportItem, UploadItem
from ingestion.services.import_publication import complete_import_item
from ingestion.services.imports import (
    ClaimedImport,
    ImportConflict,
    ManifestEntry,
    claim_import_work,
    create_import,
    finish_manifest,
    prepare_import_upload,
    record_manifest_page,
)
from ingestion.storage import ObjectIdentity, ObjectMissing, UploadGrant
from picflow.models import Event, Photo
from processing.models import PhotoProcessingState


class FakeImportStorage:
    def __init__(
        self,
        promotion_barrier: Barrier | None = None,
        promotion_hook: Callable[[], None] | None = None,
    ) -> None:
        self.objects: dict[str, ObjectIdentity] = {}
        self.deleted: list[str] = []
        self.promotion_barrier = promotion_barrier
        self.promotion_hook = promotion_hook

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        return UploadGrant("https://storage.example/upload", {"key": incoming_key}, timezone.now())

    def inspect(self, *, key: str) -> ObjectIdentity:
        try:
            return self.objects[key]
        except KeyError:
            raise ObjectMissing() from None

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes:
        del key, etag_wire
        return b"\xff\xd8" if start == 0 else b"\xff\xd9"

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity:
        del etag_wire
        self.objects[final_key] = self.objects[incoming_key]
        if self.promotion_hook is not None:
            self.promotion_hook()
        if self.promotion_barrier is not None:
            self.promotion_barrier.wait(timeout=5)
        return self.objects[final_key]

    def delete(self, *, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class ImportPublicationTests(TestCase):
    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="publication-import-owner")
        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        self.owner.user_permissions.add(permission)
        self.event = Event.objects.create(
            name="Import publication",
            slug="import-publication",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.flags = {YANDEX_DISK_IMPORT: FEATURE_FLAG_ON}
        self.storage = FakeImportStorage()

    def ready_item(self, *, submission: str, source_path: str, sha256: str = "a" * 64):
        with override_feature_flags(self.flags):
            batch = create_import(
                actor=self.owner,
                event=self.event,
                folder=None,
                submitted_source_key="submitted-key",
                submission_key=submission,
            )
            manifest = claim_import_work()
            assert manifest.attempt_id is not None
            record_manifest_page(
                batch_id=batch.id,
                attempt_id=manifest.attempt_id,
                page_number=0,
                page_fingerprint=f"page-{submission}",
                entries=(
                    ManifestEntry(
                        path=source_path,
                        name=source_path.rsplit("/", 1)[-1],
                        kind="jpeg",
                        size=100,
                        sha256=sha256,
                        version="v1",
                    ),
                ),
            )
            finish_manifest(
                batch_id=batch.id,
                attempt_id=manifest.attempt_id,
                canonical_source_key="canonical-key",
            )
            claim = claim_import_work()
            assert claim.attempt_id is not None
            assert claim.item_id is not None
            prepared = prepare_import_upload(
                attempt_id=claim.attempt_id,
                item_id=claim.item_id,
                content_sha256=sha256,
                byte_size=100,
                storage=self.storage,
            )
        item = ImportItem.objects.get(pk=claim.item_id)
        attempt = ImportAttempt.objects.get(pk=claim.attempt_id)
        with override_feature_flags(self.flags):
            replay = prepare_import_upload(
                attempt_id=claim.attempt_id,
                item_id=claim.item_id,
                content_sha256=sha256,
                byte_size=100,
                storage=self.storage,
            )
        self.assertEqual(replay.status, prepared.status)
        item.refresh_from_db()
        self.assertEqual(item.upload_attempts, 1 if prepared.status == "upload" else 0)
        if prepared.status == "upload":
            assert attempt.incoming_key is not None
            self.storage.objects[attempt.incoming_key] = ObjectIdentity(
                etag_wire='"incoming-etag"',
                etag_value="incoming-etag",
                size=100,
                content_type="image/jpeg",
            )
        return batch, claim, prepared, item

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_complete_publishes_once_and_lost_callback_returns_same_photo(self) -> None:
        batch, claim, prepared, item = self.ready_item(
            submission="publication-one", source_path="/one.jpg"
        )
        self.assertEqual(prepared.status, "upload")

        with override_feature_flags(self.flags):
            first = complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
            )
            replay = complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
                clock=lambda: claim.lease_expires_at + timedelta(days=1),
            )

        self.assertEqual(first.photo_id, replay.photo_id)
        self.assertEqual(Photo.objects.filter(import_items__batch_id=batch.id).count(), 1)
        self.assertEqual(UploadItem.objects.count(), 0)
        self.assertEqual(
            PhotoProcessingState.objects.filter(photo_id=first.photo_id).count(),
            1,
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_same_content_in_same_scope_is_skipped_but_other_scope_is_not(self) -> None:
        _, first_claim, _, first_item = self.ready_item(
            submission="dedupe-one", source_path="/first.jpg"
        )
        with override_feature_flags(self.flags):
            complete_import_item(
                attempt_id=first_claim.attempt_id,
                item_id=first_item.id,
                storage=self.storage,
            )

        _, second_claim, second_prepared, second_item = self.ready_item(
            submission="dedupe-two", source_path="/renamed.jpg"
        )
        self.assertEqual(second_prepared.status, "duplicate")
        self.assertEqual(Photo.objects.filter(imported_content__isnull=False).count(), 1)
        second_item.refresh_from_db()
        self.assertEqual(second_item.status, ImportItem.Status.DUPLICATE)

        other_event = Event.objects.create(
            name="Other scope",
            slug="other-scope",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        with override_feature_flags(self.flags):
            other = create_import(
                actor=self.owner,
                event=other_event,
                folder=None,
                submitted_source_key="submitted-key",
                submission_key="other-scope",
            )
            manifest = claim_import_work()
            assert manifest.attempt_id is not None
            record_manifest_page(
                batch_id=other.id,
                attempt_id=manifest.attempt_id,
                page_number=0,
                page_fingerprint="other-page",
                entries=(
                    ManifestEntry(
                        path="/first.jpg", name="first.jpg", kind="jpeg", size=100, sha256="a" * 64
                    ),
                ),
            )
            finish_manifest(
                batch_id=other.id,
                attempt_id=manifest.attempt_id,
                canonical_source_key="canonical-key",
            )
            other_claim = claim_import_work()
            assert other_claim.attempt_id is not None
            assert other_claim.item_id is not None
            other_prepared = prepare_import_upload(
                attempt_id=other_claim.attempt_id,
                item_id=other_claim.item_id,
                content_sha256="a" * 64,
                byte_size=100,
                storage=self.storage,
            )
        self.assertEqual(other_prepared.status, "upload")

    def test_changed_source_and_stale_or_paused_completion_are_rejected(self) -> None:
        _, claim, _, item = self.ready_item(submission="changed", source_path="/changed.jpg")
        self.flags[YANDEX_DISK_IMPORT] = FEATURE_FLAG_OFF
        with override_feature_flags(self.flags), self.assertRaises(ImportConflict) as paused:
            complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
            )
        self.assertEqual(paused.exception.code, "feature_paused")
        item.refresh_from_db()
        batch = ImportBatch.objects.get(pk=item.batch_id)
        self.assertEqual(batch.status, ImportBatch.Status.PAUSED)
        self.assertEqual(item.status, ImportItem.Status.UPLOADING)

        self.flags[YANDEX_DISK_IMPORT] = FEATURE_FLAG_ON
        with override_feature_flags(self.flags), self.assertRaises(ImportConflict) as stale:
            complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
                clock=lambda: claim.lease_expires_at + timedelta(seconds=1),
            )
        self.assertEqual(stale.exception.code, "stale_attempt")
        self.assertEqual(
            ImportAttempt.objects.get(pk=claim.attempt_id).status,
            ImportAttempt.Status.EXPIRED,
        )

    def test_prepare_rejects_source_that_changed_since_manifest(self) -> None:
        with override_feature_flags(self.flags):
            batch = create_import(
                actor=self.owner,
                event=self.event,
                folder=None,
                submitted_source_key="submitted-key",
                submission_key="source-changed",
            )
            manifest = claim_import_work()
            assert manifest.attempt_id is not None
            record_manifest_page(
                batch_id=batch.id,
                attempt_id=manifest.attempt_id,
                page_number=0,
                page_fingerprint="changed-page",
                entries=(
                    ManifestEntry(
                        path="/photo.jpg",
                        name="photo.jpg",
                        kind="jpeg",
                        size=100,
                        sha256="a" * 64,
                    ),
                ),
            )
            finish_manifest(
                batch_id=batch.id,
                attempt_id=manifest.attempt_id,
                canonical_source_key="canonical-key",
            )
            claim = claim_import_work()
            assert claim.attempt_id is not None
            assert claim.item_id is not None
            with self.assertRaises(ImportConflict) as raised:
                prepare_import_upload(
                    attempt_id=claim.attempt_id,
                    item_id=claim.item_id,
                    content_sha256="b" * 64,
                    byte_size=100,
                    storage=self.storage,
                )
        self.assertEqual(raised.exception.code, "source_changed")
        item = ImportItem.objects.get(pk=claim.item_id)
        attempt = ImportAttempt.objects.get(pk=claim.attempt_id)
        self.assertEqual(item.status, ImportItem.Status.ERROR)
        self.assertEqual(item.error_code, "source_changed")
        self.assertEqual(attempt.status, ImportAttempt.Status.FAILED)
        self.assertIsNotNone(attempt.terminal_at)

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_storage_time_expiry_fences_publication_without_replacement_claim(self) -> None:
        _, claim, _, item = self.ready_item(
            submission="expires-during-storage", source_path="/slow.jpg"
        )
        assert claim.lease_expires_at is not None
        current_time = [claim.lease_expires_at - timedelta(seconds=1)]
        self.storage.promotion_hook = lambda: current_time.__setitem__(
            0, claim.lease_expires_at + timedelta(seconds=1)
        )

        with override_feature_flags(self.flags), self.assertRaises(ImportConflict) as raised:
            complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
                clock=lambda: current_time[0],
            )

        self.assertEqual(raised.exception.code, "stale_attempt")
        self.assertFalse(ImportedContent.objects.filter(source_item=item).exists())
        self.assertIsNone(ImportItem.objects.get(pk=item.id).photo_id)
        self.assertEqual(
            ImportAttempt.objects.get(pk=claim.attempt_id).status,
            ImportAttempt.Status.EXPIRED,
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_attempt_keys_fence_delayed_old_upload_and_promotion(self) -> None:
        _, first_claim, _, item = self.ready_item(
            submission="attempt-fencing", source_path="/version.jpg"
        )
        assert first_claim.lease_expires_at is not None
        first_attempt = ImportAttempt.objects.get(pk=first_claim.attempt_id)
        replacement_time = first_claim.lease_expires_at + timedelta(seconds=1)
        with override_feature_flags(self.flags):
            replacement = claim_import_work(now=replacement_time)
            assert replacement.attempt_id is not None
            assert replacement.item_id == item.id
            prepare_import_upload(
                attempt_id=replacement.attempt_id,
                item_id=item.id,
                content_sha256="a" * 64,
                byte_size=100,
                storage=self.storage,
                now=replacement_time,
            )
        replacement_attempt = ImportAttempt.objects.get(pk=replacement.attempt_id)
        self.assertNotEqual(first_attempt.incoming_key, replacement_attempt.incoming_key)
        self.assertNotEqual(first_attempt.final_key, replacement_attempt.final_key)
        assert first_attempt.incoming_key is not None
        assert first_attempt.final_key is not None
        assert replacement_attempt.incoming_key is not None
        assert replacement_attempt.final_key is not None
        self.storage.objects[first_attempt.incoming_key] = ObjectIdentity(
            etag_wire='"stale-etag"',
            etag_value="stale-etag",
            size=100,
            content_type="image/jpeg",
        )
        self.storage.objects[replacement_attempt.incoming_key] = ObjectIdentity(
            etag_wire='"replacement-etag"',
            etag_value="replacement-etag",
            size=100,
            content_type="image/jpeg",
        )

        with override_feature_flags(self.flags):
            completed = complete_import_item(
                attempt_id=replacement.attempt_id,
                item_id=item.id,
                storage=self.storage,
                clock=lambda: replacement_time + timedelta(seconds=1),
            )
        photo = Photo.objects.get(pk=completed.photo_id)
        self.assertEqual(photo.original_key, replacement_attempt.final_key)
        self.storage.promote(
            incoming_key=first_attempt.incoming_key,
            final_key=first_attempt.final_key,
            etag_wire='"stale-etag"',
        )
        photo.refresh_from_db()
        self.assertEqual(photo.original_key, replacement_attempt.final_key)
        self.assertEqual(self.storage.objects[photo.original_key].etag_value, "replacement-etag")

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_copied_final_recovers_when_incoming_is_missing(self) -> None:
        _, claim, _, item = self.ready_item(submission="recover-final", source_path="/recover.jpg")
        attempt = ImportAttempt.objects.get(pk=claim.attempt_id)
        assert attempt.incoming_key is not None
        assert attempt.final_key is not None
        source = self.storage.objects[attempt.incoming_key]
        attempt.verified_source_etag = source.etag_value
        attempt.save(update_fields=["verified_source_etag"])
        self.storage.promote(
            incoming_key=attempt.incoming_key,
            final_key=attempt.final_key,
            etag_wire=source.etag_wire,
        )
        self.storage.delete(key=attempt.incoming_key)

        with override_feature_flags(self.flags):
            completed = complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
            )

        self.assertEqual(Photo.objects.get(pk=completed.photo_id).original_key, attempt.final_key)

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_verified_final_is_not_replaced_by_changed_incoming(self) -> None:
        _, claim, _, item = self.ready_item(submission="keep-final", source_path="/keep.jpg")
        attempt = ImportAttempt.objects.get(pk=claim.attempt_id)
        assert attempt.incoming_key is not None
        assert attempt.final_key is not None
        source = self.storage.objects[attempt.incoming_key]
        attempt.verified_source_etag = source.etag_value
        attempt.save(update_fields=["verified_source_etag"])
        self.storage.promote(
            incoming_key=attempt.incoming_key,
            final_key=attempt.final_key,
            etag_wire=source.etag_wire,
        )
        self.storage.objects[attempt.incoming_key] = ObjectIdentity(
            etag_wire='"changed-etag"',
            etag_value="changed-etag",
            size=100,
            content_type="image/jpeg",
        )

        with override_feature_flags(self.flags):
            completed = complete_import_item(
                attempt_id=claim.attempt_id,
                item_id=item.id,
                storage=self.storage,
            )

        photo = Photo.objects.get(pk=completed.photo_id)
        self.assertEqual(photo.original_key, attempt.final_key)
        self.assertEqual(self.storage.objects[photo.original_key].etag_value, source.etag_value)


class ConcurrentImportPublicationTests(TransactionTestCase):
    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=False)
    def test_competing_scope_publications_create_one_photo_and_one_enrollment(self) -> None:
        owner = get_user_model().objects.create_user(username="concurrent-import-owner")
        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        owner.user_permissions.add(permission)
        event = Event.objects.create(
            name="Concurrent import publication",
            slug="concurrent-import-publication",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        flags = {YANDEX_DISK_IMPORT: FEATURE_FLAG_ON}
        storage = FakeImportStorage(promotion_barrier=Barrier(2))
        claims: list[tuple[ClaimedImport, ImportItem]] = []

        with override_feature_flags(flags):
            for index in range(2):
                batch = create_import(
                    actor=owner,
                    event=event,
                    folder=None,
                    submitted_source_key="submitted-key",
                    submission_key=f"concurrent-{index}",
                )
                manifest = claim_import_work()
                assert manifest.attempt_id is not None
                record_manifest_page(
                    batch_id=batch.id,
                    attempt_id=manifest.attempt_id,
                    page_number=0,
                    page_fingerprint=f"concurrent-page-{index}",
                    entries=(
                        ManifestEntry(
                            path=f"/copy-{index}.jpg",
                            name=f"copy-{index}.jpg",
                            kind="jpeg",
                            size=100,
                            sha256="c" * 64,
                        ),
                    ),
                )
                finish_manifest(
                    batch_id=batch.id,
                    attempt_id=manifest.attempt_id,
                    canonical_source_key="concurrent-canonical-key",
                )
                claim = claim_import_work()
                assert claim.attempt_id is not None
                assert claim.item_id is not None
                prepare_import_upload(
                    attempt_id=claim.attempt_id,
                    item_id=claim.item_id,
                    content_sha256="c" * 64,
                    byte_size=100,
                    storage=storage,
                )
                item = ImportItem.objects.get(pk=claim.item_id)
                attempt = ImportAttempt.objects.get(pk=claim.attempt_id)
                assert attempt.incoming_key is not None
                storage.objects[attempt.incoming_key] = ObjectIdentity(
                    etag_wire=f'"etag-{index}"',
                    etag_value=f"etag-{index}",
                    size=100,
                    content_type="image/jpeg",
                )
                claims.append((claim, item))

            def complete(pair: tuple[ClaimedImport, ImportItem]):
                claim, item = pair
                assert claim.attempt_id is not None
                close_old_connections()
                try:
                    return complete_import_item(
                        attempt_id=claim.attempt_id,
                        item_id=item.id,
                        storage=storage,
                    )
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(complete, claims))

        self.assertEqual(ImportedContent.objects.count(), 1)
        self.assertEqual(
            {result.photo_id for result in results}, {ImportedContent.objects.get().photo_id}
        )
        self.assertEqual(
            set(ImportItem.objects.values_list("status", flat=True)),
            {ImportItem.Status.IMPORTED, ImportItem.Status.DUPLICATE},
        )
        self.assertEqual(
            PhotoProcessingState.objects.filter(
                photo_id=ImportedContent.objects.get().photo_id,
                processor_type="capture_metadata",
            ).count(),
            1,
        )
