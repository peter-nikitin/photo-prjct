from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from threading import Event as ThreadEvent
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from django.utils import timezone
from ingestion.models import UploadBatch, UploadItem
from ingestion.services.batches import (
    AuthorizationReason,
    BatchConflict,
    BatchInput,
    ItemInput,
    ItemStateConflict,
    authorize_item,
    classify_failure,
    create_batch,
    derive_batch_status,
    manual_retry_item,
    register_items,
    report_item_failed,
)
from ingestion.storage import ObjectChanged, ObjectMissing, StorageUnavailable, UploadGrant
from picflow.models import Event


class FakeStorage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        self.calls.append((incoming_key, max_bytes))
        sequence = len(self.calls)
        return UploadGrant(
            url="https://upload.example.test",
            fields={"policy": f"write-only-{sequence}"},
            expires_at=timezone.now() + timedelta(minutes=10 + sequence),
        )


class BatchServiceTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="photographer")
        self.other = get_user_model().objects.create_user(username="other")
        self.event = Event.objects.create(
            name="Race", slug="race", start_date=date.today(), end_date=date.today(), city="Moscow"
        )

    def make_batch(self, count: int = 1):
        return create_batch(
            uploader=self.user,
            event=self.event,
            data=BatchInput(expected_item_count=count),
        )

    def item_input(
        self,
        client_item_id: UUID | None = None,
        *,
        filename: str = "finish.jpg",
        content_type: str = "image/jpeg",
        size: int = 1234,
    ) -> ItemInput:
        return ItemInput(client_item_id or uuid4(), filename, content_type, size)

    def register_one(self, batch_id: UUID, item=None):
        result = register_items(
            uploader=self.user, batch_id=batch_id, items=[item or self.item_input()]
        )
        return result.items[0]

    def test_results_are_immutable_and_count_is_validated(self) -> None:
        result = self.make_batch()
        with self.assertRaises(FrozenInstanceError):
            result.status = "failed"
        for count in (0, 10_001):
            with self.subTest(count=count), self.assertRaises(BatchConflict):
                create_batch(
                    uploader=self.user,
                    event=self.event,
                    data=BatchInput(expected_item_count=count),
                )

    def test_registration_is_idempotent_and_keys_are_server_owned(self) -> None:
        batch = self.make_batch()
        client_id = uuid4()
        request = self.item_input(client_id)
        first = register_items(uploader=self.user, batch_id=batch.id, items=[request])
        replay = register_items(uploader=self.user, batch_id=batch.id, items=[request])

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.items, replay.items)
        row = UploadItem.objects.get(id=first.items[0].id)
        self.assertEqual(row.incoming_key, f"incoming/{batch.id}/{row.id}")
        self.assertEqual(row.final_key, f"originals/{row.id.hex}")
        self.assertNotIn(row.original_filename, row.incoming_key)

    def test_registration_conflicting_metadata_and_chunk_limits(self) -> None:
        batch = self.make_batch(100)
        client_id = uuid4()
        self.register_one(batch.id, self.item_input(client_id))
        with self.assertRaises(BatchConflict) as raised:
            register_items(
                uploader=self.user,
                batch_id=batch.id,
                items=[self.item_input(client_id, size=999)],
            )
        self.assertEqual(raised.exception.code, "item_metadata_conflict")
        with self.assertRaises(BatchConflict):
            register_items(uploader=self.user, batch_id=batch.id, items=[])
        with self.assertRaises(BatchConflict):
            register_items(
                uploader=self.user,
                batch_id=batch.id,
                items=[self.item_input() for _ in range(101)],
            )

    def test_registration_hides_other_users_batch(self) -> None:
        batch = self.make_batch()
        with self.assertRaises(UploadBatch.DoesNotExist):
            register_items(uploader=self.other, batch_id=batch.id, items=[self.item_input()])

    def test_authorization_attempts_refresh_and_limit(self) -> None:
        storage = FakeStorage()
        batch = self.make_batch()
        item = self.register_one(batch.id)

        first = authorize_item(
            uploader=self.user,
            batch_id=batch.id,
            item_id=item.id,
            reason=AuthorizationReason.DATA_ATTEMPT,
            storage=storage,
        )
        refreshed = authorize_item(
            uploader=self.user,
            batch_id=batch.id,
            item_id=item.id,
            reason=AuthorizationReason.GRANT_REFRESH,
            storage=storage,
        )
        self.assertEqual(first.item.attempt, 1)
        self.assertEqual(refreshed.item.attempt, 1)
        self.assertEqual(first.grant.fields, {"policy": "write-only-1"})
        self.assertEqual(refreshed.grant.fields, {"policy": "write-only-2"})
        self.assertNotEqual(first.grant.expires_at, refreshed.grant.expires_at)
        persisted = UploadItem.objects.get(id=item.id)
        self.assertEqual(persisted.authorization_expires_at, refreshed.grant.expires_at)
        self.assertEqual(persisted.upload_attempts, 1)
        for expected in (2, 3, 4):
            result = authorize_item(
                uploader=self.user,
                batch_id=batch.id,
                item_id=item.id,
                reason=AuthorizationReason.DATA_ATTEMPT,
                storage=storage,
            )
            self.assertEqual(result.item.attempt, expected)
        with self.assertRaises(ItemStateConflict) as raised:
            authorize_item(
                uploader=self.user,
                batch_id=batch.id,
                item_id=item.id,
                reason=AuthorizationReason.DATA_ATTEMPT,
                storage=storage,
            )
        self.assertEqual(raised.exception.code, "transfer_retries_exhausted")
        self.assertEqual(UploadBatch.objects.get(id=batch.id).status, UploadBatch.Status.UPLOADING)

    def test_authorization_rejects_failed_until_manual_retry_and_uploaded_forever(self) -> None:
        storage = FakeStorage()
        batch = self.make_batch()
        item = self.register_one(batch.id)
        report_item_failed(
            uploader=self.user,
            batch_id=batch.id,
            item_id=item.id,
            code="transfer_cancelled",
        )
        with self.assertRaises(BatchConflict):
            authorize_item(
                uploader=self.user,
                batch_id=batch.id,
                item_id=item.id,
                reason=AuthorizationReason.DATA_ATTEMPT,
                storage=storage,
            )
        retried = manual_retry_item(
            uploader=self.user,
            batch_id=batch.id,
            item_id=item.id,
            storage=storage,
        )
        self.assertEqual(retried.item.attempt, 1)
        row = UploadItem.objects.get(id=item.id)
        self.assertEqual(row.error_code, "")
        self.assertEqual(row.sanitized_error_message, "")
        self.assertEqual(UploadBatch.objects.get(id=batch.id).status, UploadBatch.Status.UPLOADING)

        row.status = UploadItem.Status.UPLOADED
        row.save(update_fields=["status"])
        with self.assertRaises(ItemStateConflict):
            report_item_failed(
                uploader=self.user,
                batch_id=batch.id,
                item_id=item.id,
                code="transfer_retries_exhausted",
            )
        with self.assertRaises(ItemStateConflict):
            manual_retry_item(
                uploader=self.user, batch_id=batch.id, item_id=item.id, storage=storage
            )

    def test_finalize_requires_all_registrations_and_terminal_items(self) -> None:
        batch = self.make_batch(2)
        item = self.register_one(batch.id)
        with self.assertRaises(BatchConflict) as missing:
            derive_batch_status(uploader=self.user, batch_id=batch.id, finalize=True)
        self.assertEqual(missing.exception.code, "missing_registrations")

        self.register_one(batch.id)
        with self.assertRaises(BatchConflict) as active:
            derive_batch_status(uploader=self.user, batch_id=batch.id, finalize=True)
        self.assertEqual(active.exception.code, "items_not_terminal")
        report_item_failed(
            uploader=self.user, batch_id=batch.id, item_id=item.id, code="transfer_cancelled"
        )

    def test_terminal_batches_reject_registration_and_authorization_without_reopening(self) -> None:
        storage = FakeStorage()
        failed_batch = self.make_batch(2)
        failed_item = self.register_one(failed_batch.id)
        UploadItem.objects.filter(id=failed_item.id).update(status=UploadItem.Status.FAILED)
        UploadBatch.objects.filter(id=failed_batch.id).update(status=UploadBatch.Status.FAILED)

        with self.assertRaises(BatchConflict):
            register_items(
                uploader=self.user,
                batch_id=failed_batch.id,
                items=[self.item_input()],
            )
        with self.assertRaises(BatchConflict):
            authorize_item(
                uploader=self.user,
                batch_id=failed_batch.id,
                item_id=failed_item.id,
                reason=AuthorizationReason.DATA_ATTEMPT,
                storage=storage,
            )
        self.assertEqual(
            UploadBatch.objects.get(id=failed_batch.id).status, UploadBatch.Status.FAILED
        )

        partial_batch = self.make_batch(2)
        partial_items = [self.register_one(partial_batch.id) for _ in range(2)]
        UploadItem.objects.filter(id=partial_items[0].id).update(status=UploadItem.Status.UPLOADED)
        UploadItem.objects.filter(id=partial_items[1].id).update(status=UploadItem.Status.FAILED)
        derive_batch_status(uploader=self.user, batch_id=partial_batch.id, finalize=True)

        with self.assertRaises(BatchConflict):
            register_items(
                uploader=self.user,
                batch_id=partial_batch.id,
                items=[self.item_input(partial_items[0].client_item_id)],
            )
        UploadItem.objects.filter(id=partial_items[1].id).update(status=UploadItem.Status.PENDING)
        with self.assertRaises(BatchConflict):
            authorize_item(
                uploader=self.user,
                batch_id=partial_batch.id,
                item_id=partial_items[1].id,
                reason=AuthorizationReason.DATA_ATTEMPT,
                storage=storage,
            )
        derived = derive_batch_status(uploader=self.user, batch_id=partial_batch.id, finalize=False)
        self.assertEqual(derived.status, UploadBatch.Status.PARTIAL)

    def test_manual_retry_is_the_only_transition_reopening_partial_batch(self) -> None:
        storage = FakeStorage()
        batch = self.make_batch(2)
        uploaded, failed = [self.register_one(batch.id) for _ in range(2)]
        UploadItem.objects.filter(id=uploaded.id).update(status=UploadItem.Status.UPLOADED)
        report_item_failed(
            uploader=self.user,
            batch_id=batch.id,
            item_id=failed.id,
            code="transfer_cancelled",
        )
        self.assertEqual(UploadBatch.objects.get(id=batch.id).status, UploadBatch.Status.PARTIAL)

        retried = manual_retry_item(
            uploader=self.user,
            batch_id=batch.id,
            item_id=failed.id,
            storage=storage,
        )
        self.assertEqual(retried.item.attempt, 1)
        self.assertEqual(UploadBatch.objects.get(id=batch.id).status, UploadBatch.Status.UPLOADING)

    def test_derive_completed_partial_and_failed(self) -> None:
        for uploaded, expected_status in (
            (2, UploadBatch.Status.COMPLETED),
            (1, UploadBatch.Status.PARTIAL),
            (0, UploadBatch.Status.FAILED),
        ):
            with self.subTest(uploaded=uploaded):
                batch = self.make_batch(2)
                items = [self.register_one(batch.id) for _ in range(2)]
                for index, item in enumerate(items):
                    row = UploadItem.objects.get(id=item.id)
                    row.status = (
                        UploadItem.Status.UPLOADED if index < uploaded else UploadItem.Status.FAILED
                    )
                    row.save(update_fields=["status"])
                result = derive_batch_status(uploader=self.user, batch_id=batch.id, finalize=True)
                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.uploaded, uploaded)
                self.assertEqual(result.failed, 2 - uploaded)

    def test_failure_classification_is_stable_and_sanitized(self) -> None:
        self.assertEqual(classify_failure(TimeoutError()).code, "storage_unavailable")
        self.assertTrue(classify_failure(TimeoutError()).retryable)
        self.assertEqual(classify_failure(ObjectChanged()).code, "object_changed")
        self.assertTrue(classify_failure(ObjectChanged()).retryable)
        self.assertEqual(classify_failure(ObjectMissing()).code, "object_missing")
        self.assertEqual(classify_failure(StorageUnavailable()).code, "storage_unavailable")
        self.assertEqual(classify_failure(403).action, AuthorizationReason.GRANT_REFRESH)
        self.assertEqual(classify_failure(408).action, AuthorizationReason.DATA_ATTEMPT)
        self.assertEqual(classify_failure(429).action, AuthorizationReason.DATA_ATTEMPT)
        self.assertEqual(classify_failure(503).action, AuthorizationReason.DATA_ATTEMPT)
        self.assertFalse(classify_failure("size_mismatch").retryable)
        self.assertEqual(
            classify_failure(RuntimeError("private diagnostic")).code, "internal_error"
        )
        self.assertEqual(classify_failure({"private": "diagnostic"}).code, "internal_error")


class RegistrationConcurrencyTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="racer")
        self.event = Event.objects.create(
            name="Concurrent",
            slug="concurrent",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.batch = UploadBatch.objects.create(
            event=self.event, uploader=self.user, expected_item_count=1
        )

    def race_register(self, client_item_id):
        close_old_connections()
        worker_connection = connections["default"]
        try:
            return register_items(
                uploader=self.user,
                batch_id=self.batch.id,
                items=[ItemInput(client_item_id, "race.jpg", "image/jpeg", 100)],
            )
        except BatchConflict:
            return None
        finally:
            worker_connection.close()

    def test_row_lock_prevents_exceeding_count_with_separate_connections(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.race_register, [uuid4(), uuid4()]))
        self.assertEqual(UploadItem.objects.filter(batch=self.batch).count(), 1)
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_racing_same_uuid_converges_to_one_stable_row(self) -> None:
        client_id = uuid4()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.race_register, [client_id, client_id]))
        self.assertEqual(UploadItem.objects.filter(batch=self.batch).count(), 1)
        self.assertTrue(all(result is not None for result in results))
        self.assertEqual(results[0].items, results[1].items)

    def test_finalization_waits_for_registration_lock_and_sees_committed_items(self) -> None:
        batch = UploadBatch.objects.create(
            event=self.event, uploader=self.user, expected_item_count=2
        )
        first = ItemInput(uuid4(), "first.jpg", "image/jpeg", 100)
        register_items(uploader=self.user, batch_id=batch.id, items=[first])
        second = ItemInput(uuid4(), "second.jpg", "image/jpeg", 100)
        lock_held = ThreadEvent()
        release_registration = ThreadEvent()

        from ingestion.services import batches as batch_services

        original_matches = batch_services._metadata_matches

        def pause_with_batch_lock(row, item):
            lock_held.set()
            self.assertTrue(release_registration.wait(timeout=5))
            return original_matches(row, item)

        def finalize():
            close_old_connections()
            worker_connection = connections["default"]
            try:
                derive_batch_status(uploader=self.user, batch_id=batch.id, finalize=True)
            except BatchConflict as error:
                return error.code
            finally:
                worker_connection.close()
            return "unexpected_success"

        def register_in_worker():
            close_old_connections()
            worker_connection = connections["default"]
            try:
                return register_items(
                    uploader=self.user,
                    batch_id=batch.id,
                    items=[first, second],
                )
            finally:
                worker_connection.close()

        with (
            patch.object(batch_services, "_metadata_matches", side_effect=pause_with_batch_lock),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            registration = pool.submit(register_in_worker)
            self.assertTrue(lock_held.wait(timeout=5))
            finalization = pool.submit(finalize)
            with self.assertRaises(FutureTimeout):
                finalization.result(timeout=0.1)
            release_registration.set()
            registration.result(timeout=5)
            self.assertEqual(finalization.result(timeout=5), "items_not_terminal")

        self.assertEqual(UploadItem.objects.filter(batch=batch).count(), 2)
