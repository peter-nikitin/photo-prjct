from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from django.utils import timezone
from ingestion.models import ImportAttempt, ImportBatch, ImportItem
from picflow.models import Event, Photo


class ImportCleanupTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.old = self.now - timedelta(hours=25)
        owner = get_user_model().objects.create(username="cleanup")
        event = Event.objects.create(
            name="Cleanup",
            slug="cleanup",
            city="Moscow",
            start_date="2026-09-07",
            end_date="2026-09-07",
        )
        self.batch = ImportBatch.objects.create(
            owner=owner,
            event=event,
            submitted_source_key="secret-source",
            submission_key="cleanup",
            status="paused",
            manifest_complete=True,
            last_activity_at=self.old,
        )
        self.item = ImportItem.objects.create(
            batch=self.batch,
            source_path="/secret.jpg",
            filename="private.jpg",
            byte_size=100,
            status="uploading",
        )
        self.attempt = ImportAttempt.objects.create(
            batch=self.batch,
            item=self.item,
            kind="file",
            claimed_at=self.old,
            heartbeat_at=self.old,
            lease_expires_at=self.old,
            incoming_key=f"incoming/{uuid4()}/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
            verified_source_etag="abc",
            verified_final_etag="abc",
        )

    def command(self, *args):
        output = StringIO()
        with patch(
            "ingestion.management.commands.cleanup_stale_imports.PrivateUploadStorage"
        ) as factory:
            call_command("cleanup_stale_imports", *args, stdout=output)
        return output.getvalue(), factory

    def test_default_dry_run_preserves_paused_work_and_hides_source(self):
        output, factory = self.command()
        factory.assert_not_called()
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, "active")
        self.assertIn("eligible=1", output)
        self.assertNotIn("secret", output)

    def test_apply_expires_interrupted_final_checkpoint_without_losing_manifest(self):
        _, factory = self.command("--apply")
        self.assertEqual(factory.return_value.delete.call_count, 2)
        self.attempt.refresh_from_db()
        self.item.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(self.attempt.status, "expired")
        self.assertIsNone(self.attempt.final_key)
        self.assertEqual(self.attempt.verified_final_etag, "")
        self.assertEqual(self.item.status, "pending")
        self.assertTrue(self.batch.manifest_complete)
        self.assertEqual(self.item.source_path, "/secret.jpg")

    def test_live_lease_and_confirmed_photo_are_never_deleted(self):
        self.attempt.lease_expires_at = self.now + timedelta(seconds=120)
        self.attempt.save()
        _, factory = self.command("--apply")
        factory.return_value.delete.assert_not_called()
        self.attempt.lease_expires_at = self.old
        self.attempt.save()
        photo = Photo.objects.create(
            event=self.batch.event,
            original_key=self.attempt.final_key,
            uploaded_by=self.batch.owner,
            original_filename="photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=self.now,
        )
        self.item.photo = photo
        self.item.status = "imported"
        self.item.save()
        _, factory = self.command("--apply")
        self.assertNotIn(
            self.attempt.final_key,
            [c.kwargs["key"] for c in factory.return_value.delete.call_args_list],
        )

    def test_inspection_is_bounded_and_sanitized(self):
        output = StringIO()
        call_command(
            "inspect_photo_imports", "--batch", str(self.batch.pk), "--limit", "1", stdout=output
        )
        self.assertIn(str(self.batch.pk), output.getvalue())
        for forbidden in ("secret-source", "/secret.jpg", "private.jpg", "originals/"):
            self.assertNotIn(forbidden, output.getvalue())

    def test_publication_row_lock_skips_cleanup_and_failed_delete_keeps_expired_fence(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event as ThreadEvent

        from django.db import close_old_connections, transaction
        from ingestion.storage import StorageError

        locked, release = ThreadEvent(), ThreadEvent()

        def hold_publication_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    ImportItem.objects.select_for_update().get(pk=self.item.pk)
                    locked.set()
                    assert release.wait(5)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_publication_lock)
            self.assertTrue(locked.wait(5))
            try:
                _, factory = self.command("--apply")
                factory.return_value.delete.assert_not_called()
            finally:
                release.set()
            future.result()
        with patch(
            "ingestion.management.commands.cleanup_stale_imports.PrivateUploadStorage"
        ) as factory:
            factory.return_value.delete.side_effect = StorageError()
            from django.core.management.base import CommandError

            with self.assertRaises(CommandError):
                call_command("cleanup_stale_imports", "--apply", stdout=StringIO())
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, "expired")
        self.assertIsNotNone(self.attempt.final_key)

    def test_restart_after_cleanup_allocates_fresh_keys(self):
        from feature_flags.registry import YANDEX_DISK_IMPORT
        from feature_flags.states import FEATURE_FLAG_ON
        from feature_flags.testing import override_feature_flags
        from ingestion.models import ImportScope
        from ingestion.services.imports import claim_import_work, prepare_import_upload

        owner = self.batch.owner
        owner.is_superuser = True
        owner.is_staff = True
        owner.save()
        self.batch.scope = ImportScope.objects.create(
            owner=owner, event=self.batch.event, canonical_source_key="scope"
        )
        self.batch.save()
        previous = (self.attempt.incoming_key, self.attempt.final_key)
        self.command("--apply")
        with override_feature_flags({YANDEX_DISK_IMPORT: FEATURE_FLAG_ON}):
            claimed = claim_import_work()
            assert claimed.attempt_id is not None
            with patch(
                "ingestion.management.commands.cleanup_stale_imports.PrivateUploadStorage"
            ) as storage:
                from ingestion.storage import UploadGrant

                storage.return_value.create_presigned_post.return_value = UploadGrant(
                    "https://storage.example", {}, self.now
                )
                prepare_import_upload(
                    attempt_id=claimed.attempt_id,
                    item_id=self.item.pk,
                    content_sha256="a" * 64,
                    byte_size=100,
                    storage=storage.return_value,
                )
        current = ImportAttempt.objects.get(pk=claimed.attempt_id)
        self.assertNotEqual(current.incoming_key, previous[0])
        self.assertNotEqual(current.final_key, previous[1])

    def test_bounded_cleanup_passes_older_protected_prefix(self):
        for index in range(2):
            key = f"originals/{uuid4().hex}"
            photo = Photo.objects.create(
                id=f"protected-{index}",
                event=self.batch.event,
                original_key=key,
                uploaded_by=self.batch.owner,
                original_filename="winner.jpg",
                original_size=100,
                original_content_type="image/jpeg",
                uploaded_at=self.old,
            )
            item = ImportItem.objects.create(
                batch=self.batch,
                source_path=f"/winner-{index}.jpg",
                filename="winner.jpg",
                byte_size=100,
                status="imported",
                photo=photo,
            )
            ImportAttempt.objects.create(
                batch=self.batch,
                item=item,
                kind="file",
                status="succeeded",
                claimed_at=self.old - timedelta(hours=1),
                heartbeat_at=self.old - timedelta(hours=1),
                lease_expires_at=self.old,
                final_key=key,
            )
        orphan_keys = {self.attempt.incoming_key, self.attempt.final_key}
        _, factory = self.command("--apply", "--limit", "1")
        self.assertEqual(
            {call.kwargs["key"] for call in factory.return_value.delete.call_args_list}, orphan_keys
        )
        _, factory = self.command("--apply", "--limit", "1")
        factory.return_value.delete.assert_not_called()
        self.assertEqual(Photo.objects.filter(id__startswith="protected-").count(), 2)

    def test_duplicate_link_does_not_protect_distinct_orphan_final(self):
        from ingestion.models import ImportedContent, ImportScope

        winner_key = f"originals/{uuid4().hex}"
        photo = Photo.objects.create(
            id="winner",
            event=self.batch.event,
            original_key=winner_key,
            uploaded_by=self.batch.owner,
            original_filename="winner.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=self.old,
        )
        winner = ImportItem.objects.create(
            batch=self.batch,
            source_path="/winner.jpg",
            filename="winner.jpg",
            byte_size=100,
            status="imported",
            photo=photo,
        )
        scope = ImportScope.objects.create(
            owner=self.batch.owner, event=self.batch.event, canonical_source_key="winner-scope"
        )
        ImportedContent.objects.create(
            scope=scope, sha256="a" * 64, byte_size=100, photo=photo, source_item=winner
        )
        self.item.status = "duplicate"
        self.item.photo = photo
        self.item.save()
        self.attempt.status = "succeeded"
        self.attempt.save()
        orphan_keys = {self.attempt.incoming_key, self.attempt.final_key}
        _, factory = self.command("--apply")
        self.assertEqual(
            {call.kwargs["key"] for call in factory.return_value.delete.call_args_list}, orphan_keys
        )
        self.assertEqual(Photo.objects.get(pk=photo.pk).original_key, winner_key)
        self.assertTrue(ImportedContent.objects.filter(source_item=winner).exists())
        self.assertTrue(ImportAttempt.objects.filter(pk=self.attempt.pk).exists())
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.photo_id), ("duplicate", photo.pk))
