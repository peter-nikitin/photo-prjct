from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from ingestion.models import UploadBatch, UploadItem
from ingestion.services.batch_history import owned_event_batch_history
from picflow.tests.event_management_helpers import event, private_photo, user_with_permissions


class BatchHistoryTests(TestCase):
    def setUp(self):
        self.owner = user_with_permissions("owner")
        self.other = user_with_permissions("other")
        self.event = event()

    def batch(self, **overrides):
        return UploadBatch.objects.create(
            **{
                "event": self.event,
                "uploader": self.owner,
                "expected_item_count": 2,
                **overrides,
            }
        )

    def item(self, batch, *, photo=None, status=UploadItem.Status.UPLOADED):
        return UploadItem.objects.create(
            batch=batch,
            photo=photo,
            status=status,
            client_item_id=uuid4(),
            original_filename="secret-filename.jpg",
            declared_content_type="image/jpeg",
            expected_size=4,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
            error_code="secret-error",
            sanitized_error_message="secret-error-detail",
        )

    def test_history_scopes_before_paginating_and_includes_completed_batches(self):
        own = [self.batch(status=UploadBatch.Status.COMPLETED) for _ in range(21)]
        for _ in range(22):
            self.batch(uploader=self.other)
        self.batch(event=event("Other race"))
        UploadBatch.objects.filter(pk=own[0].pk).update(
            last_activity_at=timezone.now() + timedelta(days=1)
        )
        first = owned_event_batch_history(uploader=self.owner, event=self.event, page=1)
        second = owned_event_batch_history(uploader=self.owner, event=self.event, page=2)
        self.assertEqual(first.paginator.count, 21)
        self.assertEqual(len(first.object_list), 20)
        self.assertEqual([row.id for row in first], [b.id for b in reversed(own[1:])])
        self.assertEqual([row.id for row in second], [own[0].id])
        self.assertEqual(first[0].status, UploadBatch.Status.COMPLETED)

    def test_history_counts_confirmed_membership_and_hides_file_details(self):
        batch = self.batch(expected_item_count=3)
        confirmed = private_photo(self.event, self.owner, is_hidden=True)
        self.item(batch, photo=confirmed)
        self.item(batch)  # A transferred object without a confirmed Photo is not complete.
        self.item(batch, status=UploadItem.Status.FAILED)
        private_photo(self.event, self.owner)  # Same uploader/event is not batch membership.
        foreign = self.batch(uploader=self.other)
        self.item(foreign, photo=private_photo(self.event, self.other))
        row = owned_event_batch_history(uploader=self.owner, event=self.event)[0]
        self.assertEqual((row.confirmed_count, row.failed_count, row.unresolved_count), (1, 1, 2))
        self.assertFalse(row.can_close)
        for secret in ("secret-filename", "secret-error", "originals/", str(foreign.pk)):
            self.assertNotIn(secret, repr(row))
        self.assertNotIn(confirmed.pk, repr(row))

    def test_all_confirmed_is_safe_to_close(self):
        batch = self.batch(expected_item_count=1, status=UploadBatch.Status.COMPLETED)
        self.item(batch, photo=private_photo(self.event, self.owner))
        row = owned_event_batch_history(uploader=self.owner, event=self.event)[0]
        self.assertTrue(row.can_close)
        self.assertEqual(row.unresolved_count, 0)
        self.assertFalse(hasattr(row, "processing"))

    def test_history_does_not_project_photo_processing(self):
        batches = [self.batch() for _ in range(3)]
        for batch in batches:
            self.item(batch, photo=private_photo(self.event, self.owner))

        with CaptureQueriesContext(connection) as queries:
            rows = list(owned_event_batch_history(uploader=self.owner, event=self.event))

        self.assertEqual(len(rows), 3)
        self.assertTrue(all(not hasattr(row, "processing") for row in rows))
        self.assertFalse(
            any(
                "processing_photoprocessingstate" in query["sql"].lower()
                for query in queries.captured_queries
            )
        )
