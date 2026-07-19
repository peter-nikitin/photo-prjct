from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from ingestion.models import UploadBatch, UploadItem
from picflow.models import Event, Photo


class CleanupStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, *, key: str) -> None:
        self.deleted.append(key)


class CleanupStaleUploadsTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="cleanup-photographer")
        self.event = Event.objects.create(
            name="Cleanup event",
            slug="cleanup-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.storage = CleanupStorage()

    def batch(self, *, expected: int = 1, hours_old: int = 25) -> UploadBatch:
        batch = UploadBatch.objects.create(
            event=self.event,
            uploader=self.user,
            expected_item_count=expected,
            status=UploadBatch.Status.UPLOADING,
        )
        UploadBatch.objects.filter(pk=batch.pk).update(
            last_activity_at=timezone.now() - timedelta(hours=hours_old)
        )
        batch.refresh_from_db()
        return batch

    def item(self, batch: UploadBatch, *, status: str = "pending") -> UploadItem:
        item_id = uuid4()
        item = UploadItem.objects.create(
            id=item_id,
            batch=batch,
            client_item_id=uuid4(),
            original_filename="stale.jpg",
            declared_content_type="image/jpeg",
            expected_size=100,
            incoming_key=f"incoming/{batch.id}/{item_id}",
            final_key=f"originals/{item_id.hex}",
            status=status,
        )
        UploadItem.objects.filter(pk=item.pk).update(
            last_activity_at=timezone.now() - timedelta(hours=25)
        )
        item.refresh_from_db()
        return item

    def run_cleanup(self) -> None:
        with patch(
            "ingestion.management.commands.cleanup_stale_uploads.PrivateUploadStorage",
            return_value=self.storage,
        ):
            call_command("cleanup_stale_uploads", limit=100)

    def test_stale_active_item_is_failed_and_unlinked_objects_are_deleted(self) -> None:
        batch = self.batch()
        item = self.item(batch, status="authorized")

        self.run_cleanup()

        item.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(item.status, UploadItem.Status.FAILED)
        self.assertEqual(item.error_code, "upload_expired")
        self.assertEqual(batch.status, UploadBatch.Status.FAILED)
        self.assertCountEqual(self.storage.deleted, [item.incoming_key, item.final_key])

    def test_linked_final_object_is_preserved_and_batch_becomes_partial(self) -> None:
        batch = self.batch(expected=2)
        uploaded = self.item(batch, status="uploaded")
        photo = Photo.objects.create(
            id=uploaded.id.hex,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=uploaded.final_key,
            original_filename=uploaded.original_filename,
            original_size=uploaded.expected_size,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        uploaded.photo = photo
        uploaded.save(update_fields=["photo"])
        stale = self.item(batch)

        self.run_cleanup()

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.Status.PARTIAL)
        self.assertNotIn(uploaded.final_key, self.storage.deleted)
        self.assertIn(stale.final_key, self.storage.deleted)

    def test_recent_batch_is_not_touched(self) -> None:
        batch = self.batch(hours_old=1)
        item = self.item(batch)

        self.run_cleanup()

        item.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(item.status, UploadItem.Status.PENDING)
        self.assertEqual(batch.status, UploadBatch.Status.UPLOADING)
        self.assertEqual(self.storage.deleted, [])
