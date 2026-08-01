from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from ingestion.models import UploadBatch, UploadItem
from ingestion.services.resume import get_resume_manifest, list_unfinished_batches
from picflow.models import Event, Photo


class ResumeServiceTests(TestCase):
    def setUp(self) -> None:
        users = get_user_model().objects
        self.uploader = users.create_user(username="photographer")
        self.other = users.create_user(username="other")
        self.event = Event.objects.create(
            name="Race",
            slug="resume-race",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def batch(self, *, uploader=None, status=UploadBatch.Status.CREATED, expected=3) -> UploadBatch:
        return UploadBatch.objects.create(
            uploader=uploader or self.uploader,
            event=self.event,
            expected_item_count=expected,
            status=status,
        )

    def item(
        self,
        batch: UploadBatch,
        *,
        status=UploadItem.Status.PENDING,
        confirmed: bool = False,
        filename: str = "race.jpg",
        size: int = 4,
        last_modified_ms: int | None = 1_722_500_123_456,
        ambiguous_sha256: str | None = None,
    ) -> UploadItem:
        item = UploadItem.objects.create(
            batch=batch,
            client_item_id=uuid4(),
            original_filename=filename,
            declared_content_type="image/jpeg",
            expected_size=size,
            client_last_modified_ms=last_modified_ms,
            ambiguous_sha256=ambiguous_sha256,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
            status=status,
        )
        if confirmed:
            photo = Photo.objects.create(
                id=item.id.hex,
                event=self.event,
                uploaded_by=self.uploader,
                original_key=item.final_key,
                original_filename=item.original_filename,
                original_size=item.expected_size,
                original_content_type=item.declared_content_type,
                uploaded_at=timezone.now(),
            )
            item.photo = photo
            item.status = UploadItem.Status.UPLOADED
            item.save(update_fields=["photo", "status"])
        return item

    def test_list_returns_owned_unfinished_batches_by_newest_activity_with_counts(self) -> None:
        older = self.batch(status=UploadBatch.Status.PARTIAL, expected=4)
        self.item(older, confirmed=True)
        self.item(older, status=UploadItem.Status.FAILED, filename="failed.jpg")
        self.item(older, filename="waiting.jpg")
        newer = self.batch(status=UploadBatch.Status.UPLOADING, expected=2)
        self.item(newer, filename="new.jpg")
        UploadBatch.objects.filter(pk=older.pk).update(
            last_activity_at=timezone.now() - timedelta(hours=1)
        )

        summaries = list_unfinished_batches(self.uploader)

        self.assertEqual([summary.id for summary in summaries], [newer.id, older.id])
        self.assertEqual(summaries[0].event_name, self.event.name)
        self.assertEqual(summaries[0].expected_count, 2)
        self.assertEqual(summaries[0].confirmed_count, 0)
        self.assertEqual(summaries[0].failed_count, 0)
        self.assertEqual(summaries[0].unresolved_count, 2)
        self.assertEqual(summaries[1].expected_count, 4)
        self.assertEqual(summaries[1].confirmed_count, 1)
        self.assertEqual(summaries[1].failed_count, 1)
        self.assertEqual(summaries[1].unresolved_count, 3)
        with self.assertRaises(FrozenInstanceError):
            summaries[0].status = UploadBatch.Status.FAILED

    def test_list_excludes_completed_fully_confirmed_and_other_owner_batches(self) -> None:
        completed = self.batch(status=UploadBatch.Status.COMPLETED, expected=1)
        self.item(completed, confirmed=True)
        fully_confirmed = self.batch(status=UploadBatch.Status.FAILED, expected=1)
        self.item(fully_confirmed, confirmed=True)
        foreign = self.batch(uploader=self.other, status=UploadBatch.Status.UPLOADING, expected=1)
        self.item(foreign)
        resumable = self.batch(status=UploadBatch.Status.FAILED, expected=1)
        self.item(resumable, status=UploadItem.Status.FAILED)

        summaries = list_unfinished_batches(self.uploader)

        self.assertEqual([summary.id for summary in summaries], [resumable.id])

    def test_list_includes_each_resumable_status_and_uses_one_aggregate_query(self) -> None:
        for index, status in enumerate(
            (
                UploadBatch.Status.CREATED,
                UploadBatch.Status.UPLOADING,
                UploadBatch.Status.PARTIAL,
                UploadBatch.Status.FAILED,
            )
        ):
            batch = self.batch(status=status, expected=3)
            self.item(batch, filename=f"{index}-pending.jpg")
            self.item(batch, status=UploadItem.Status.FAILED, filename=f"{index}-failed.jpg")
            self.item(batch, confirmed=True, filename=f"{index}-confirmed.jpg")

        with self.assertNumQueries(1):
            summaries = list_unfinished_batches(self.uploader)

        self.assertEqual(len(summaries), 4)
        self.assertEqual(
            {
                (summary.confirmed_count, summary.failed_count, summary.unresolved_count)
                for summary in summaries
            },
            {(1, 1, 2)},
        )

    def test_manifest_exposes_only_matching_state_for_one_owned_unfinished_batch(self) -> None:
        batch = self.batch(status=UploadBatch.Status.PARTIAL, expected=2)
        pending = self.item(batch, filename="pending.jpg", size=7)
        uploaded = self.item(
            batch,
            confirmed=True,
            filename="uploaded.jpg",
            size=9,
            ambiguous_sha256="a" * 64,
        )

        with self.assertNumQueries(2):
            manifest = get_resume_manifest(self.uploader, batch.id)

        self.assertEqual(manifest.id, batch.id)
        self.assertEqual(manifest.event_id, self.event.id)
        self.assertEqual(manifest.event_name, self.event.name)
        self.assertEqual(manifest.expected_count, 2)
        items = {item.id: item for item in manifest.items}
        self.assertEqual(set(items), {pending.id, uploaded.id})
        self.assertEqual(items[pending.id].filename, "pending.jpg")
        self.assertEqual(items[pending.id].size, 7)
        self.assertEqual(items[pending.id].last_modified_ms, 1_722_500_123_456)
        self.assertIsNone(items[pending.id].ambiguous_sha256)
        self.assertEqual(items[pending.id].status, UploadItem.Status.PENDING)
        self.assertFalse(items[pending.id].confirmed)
        self.assertEqual(items[uploaded.id].ambiguous_sha256, "a" * 64)
        self.assertTrue(items[uploaded.id].confirmed)
        with self.assertRaises(FrozenInstanceError):
            items[pending.id].filename = "changed.jpg"

    def test_manifest_hides_missing_cross_owner_completed_and_fully_confirmed_batches(self) -> None:
        owned = self.batch(status=UploadBatch.Status.UPLOADING, expected=1)
        self.item(owned)
        foreign = self.batch(uploader=self.other, status=UploadBatch.Status.UPLOADING, expected=1)
        self.item(foreign)
        completed = self.batch(status=UploadBatch.Status.COMPLETED, expected=1)
        self.item(completed)
        fully_confirmed = self.batch(status=UploadBatch.Status.FAILED, expected=1)
        self.item(fully_confirmed, confirmed=True)

        self.assertEqual(get_resume_manifest(self.uploader, owned.id).id, owned.id)
        for batch_id in (foreign.id, completed.id, fully_confirmed.id, uuid4()):
            with self.subTest(batch_id=batch_id), self.assertRaises(UploadBatch.DoesNotExist):
                get_resume_manifest(self.uploader, batch_id)
