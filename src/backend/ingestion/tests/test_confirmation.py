from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from threading import Event as ThreadEvent
from threading import current_thread
from typing import cast
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection
from django.test import Client, TransactionTestCase, modify_settings, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.states import FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.models import UploadBatch, UploadItem
from ingestion.services.batches import (
    AuthorizationReason,
    BatchInput,
    ItemInput,
    ItemStateConflict,
    authorize_item,
    create_batch,
    register_items,
)
from ingestion.services.confirmation import confirm_upload_item
from ingestion.storage import (
    ObjectChanged,
    ObjectIdentity,
    ObjectMissing,
    StorageUnavailable,
    UploadGrant,
)
from picflow.models import Event, EventFolder, Photo
from PIL import Image
from processing.models import (
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import GENERATE_PREVIEW_CONFIGURATION


class SimulatedCrash(RuntimeError):
    pass


class ConfirmationStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.failures: dict[str, Exception] = {}
        self.delete_failure: Exception | None = None
        self.promote_etags: list[str] = []
        self.geometry_read_atomic_states: list[bool] = []
        self.final_after_promote: tuple[bytes, str, str] | None = None

    def add(self, key: str, content: bytes, etag_wire: str = '"source-etag"') -> None:
        self.objects[key] = (content, etag_wire, "image/jpeg")

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        return UploadGrant("https://upload.test", {}, timezone.now() + timedelta(minutes=10))

    def inspect(self, *, key: str) -> ObjectIdentity:
        self.calls.append(("inspect", key))
        self._raise("inspect")
        try:
            content, etag, content_type = self.objects[key]
        except KeyError:
            raise ObjectMissing() from None
        return ObjectIdentity(etag, etag.strip('"'), len(content), content_type)

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes:
        self.calls.append(("read", key))
        self._raise("read")
        content, actual_etag, _ = self.objects[key]
        if etag_wire != actual_etag:
            raise ObjectChanged()
        if end - start > 2:
            self.geometry_read_atomic_states.append(connection.in_atomic_block)
        return content[start : end + 1]

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity:
        self.calls.append(("promote", incoming_key))
        self._raise("promote")
        content, actual_etag, content_type = self.objects[incoming_key]
        if etag_wire != actual_etag:
            raise ObjectChanged()
        self.promote_etags.append(etag_wire)
        self.objects[final_key] = (bytes(content), actual_etag, content_type)
        if self.final_after_promote is not None:
            self.objects[final_key] = self.final_after_promote
            self.final_after_promote = None
        return self.inspect(key=final_key)

    def delete(self, *, key: str) -> None:
        self.calls.append(("delete", key))
        if self.delete_failure is not None:
            raise self.delete_failure
        self.objects.pop(key, None)

    def _raise(self, operation: str) -> None:
        failure = self.failures.pop(operation, None)
        if failure is not None:
            raise failure


class ConfirmationTests(TransactionTestCase):
    jpeg = b"\xff\xd8photo payload\xff\xd9"

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="photographer")
        self.other = get_user_model().objects.create_user(username="other")
        self.event = Event.objects.create(
            name="Finish",
            slug="finish",
            start_date=date.today(),
            end_date=date.today(),
            city="M",
            timezone_name="Europe/Moscow",
        )
        batch = create_batch(
            uploader=self.user,
            event=self.event,
            data=BatchInput(expected_item_count=1),
        )
        registered = register_items(
            uploader=self.user,
            batch_id=batch.id,
            items=[ItemInput(uuid4(), "finish.jpg", "image/jpeg", len(self.jpeg), None)],
        )
        self.batch_id = batch.id
        self.item_id = registered.items[0].id
        authorize_item(
            uploader=self.user,
            batch_id=self.batch_id,
            item_id=self.item_id,
            reason=AuthorizationReason.DATA_ATTEMPT,
            storage=ConfirmationStorage(),
        )
        self.storage = ConfirmationStorage()
        self.item = UploadItem.objects.get(id=self.item_id)
        self.storage.add(self.item.incoming_key, self.jpeg)

    def confirm(self, failpoint: Callable[[str], None] | None = None) -> Photo | None:
        return confirm_upload_item(
            uploader=self.user,
            batch_id=self.batch_id,
            item_id=self.item_id,
            storage=self.storage,
            failpoint=failpoint,
        )

    def confirm_success(self, failpoint: Callable[[str], None] | None = None) -> Photo:
        photo = self.confirm(failpoint)
        assert photo is not None
        return photo

    def set_preview_payload(self) -> None:
        encoded = BytesIO()
        image = Image.new("RGB", (40, 20), "white")
        try:
            image.save(encoded, "JPEG")
        finally:
            image.close()
        payload = encoded.getvalue()
        UploadItem.objects.filter(pk=self.item_id).update(expected_size=len(payload))
        self.storage.add(self.item.incoming_key, payload)

    def admin_event_change_data(self, *, access_type: str) -> dict[str, object]:
        return {
            "name": self.event.name,
            "slug": self.event.slug,
            "start_date": self.event.start_date,
            "end_date": self.event.end_date,
            "city": self.event.city,
            "description": self.event.description,
            "access_type": access_type,
            "price_per_photo_rub": "300.00" if access_type == Event.AccessType.PAID else "",
            "publication_status": self.event.publication_status,
            "timezone_name": self.event.timezone_name or "",
            "folders-TOTAL_FORMS": "0",
            "folders-INITIAL_FORMS": "0",
            "folders-MIN_NUM_FORMS": "0",
            "folders-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }

    def change_event_access_in_admin(self, *, access_type: str):
        close_old_connections()
        try:
            admin_user = get_user_model().objects.get(username="race-admin")
            client = Client()
            client.force_login(admin_user)
            return client.post(
                reverse("admin:picflow_event_change", args=[self.event.pk]),
                self.admin_event_change_data(access_type=access_type),
            )
        finally:
            close_old_connections()

    def assert_completed_once(self, photo: Photo) -> None:
        item = UploadItem.objects.get(id=self.item_id)
        batch = UploadBatch.objects.get(id=self.batch_id)
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(item.photo_id, photo.pk)
        self.assertEqual(item.status, UploadItem.Status.UPLOADED)
        self.assertEqual(photo.pk, item.id.hex)
        self.assertEqual(photo.src.name, "")
        self.assertEqual(photo.event, self.event)
        self.assertEqual(photo.uploaded_by, self.user)
        self.assertEqual(photo.original_key, item.final_key)
        self.assertEqual(photo.original_filename, "finish.jpg")
        self.assertEqual(photo.original_size, len(self.jpeg))
        self.assertEqual(photo.original_content_type, "image/jpeg")
        self.assertIsNotNone(photo.uploaded_at)
        self.assertEqual(batch.status, UploadBatch.Status.COMPLETED)
        self.assertEqual(len([key for key in self.storage.objects if key == item.final_key]), 1)

    def test_success_and_repeated_confirmation_are_idempotent(self) -> None:
        first = self.confirm_success()
        self.storage.add(self.item.incoming_key, b"malicious replacement", '"replacement"')
        second = self.confirm_success()
        self.assertEqual(first.pk, second.pk)
        self.assertIsNone(second.folder_id)
        self.assert_completed_once(second)
        self.assertEqual(sum(call[0] == "promote" for call in self.storage.calls), 1)
        item = UploadItem.objects.get(id=self.item_id)
        self.assertEqual(item.verified_source_etag, "source-etag")
        self.assertEqual(self.storage.promote_etags, ['"source-etag"'])
        self.assertEqual(self.storage.objects[item.final_key][0], self.jpeg)

    def test_confirmation_copies_the_registered_folder_to_photo(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Старт")
        UploadItem.objects.filter(pk=self.item_id).update(folder_id=folder.id)

        self.confirm_success()

        start_item = UploadItem.objects.get(pk=self.item_id)
        assert start_item.photo is not None
        self.assertEqual(start_item.photo.folder, folder)

    def test_confirmation_rejects_a_folder_from_another_event_before_creating_photo(self) -> None:
        other_event = Event.objects.create(
            name="Other",
            slug="other",
            start_date=date.today(),
            end_date=date.today(),
            city="M",
        )
        foreign_folder = EventFolder.objects.create(event=other_event, name="Старт")
        UploadItem.objects.filter(pk=self.item_id).update(folder_id=foreign_folder.id)

        with self.assertRaises(ItemStateConflict) as raised:
            self.confirm()

        self.assertEqual(raised.exception.code, "folder_event_mismatch")
        self.assertEqual(Photo.objects.count(), 0)

    def test_successful_confirmation_enrolls_the_new_private_photo(self) -> None:
        photo = self.confirm_success()

        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        self.assertEqual(state.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(state.current_run.event, self.event)
        self.assertEqual(state.current_run.status, EventProcessingRun.Status.COLLECTING)
        self.assertEqual(state.current_job.status, ProcessingJob.Status.QUEUED)
        self.assertEqual(
            state.current_job.input_fingerprint,
            {
                "original_key": self.item.final_key,
                "original_size": len(self.jpeg),
                "original_content_type": "image/jpeg",
                "verified_source_etag": "source-etag",
                "version_evidence": "verified_source_etag",
            },
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_preview_activation_persists_the_preview_first_pair_and_only_queues_preview(
        self,
    ) -> None:
        """A newly activated upload must not enqueue ML before its preview is accepted."""
        from io import BytesIO

        encoded = BytesIO()
        image = Image.new("RGB", (40, 20), "white")
        try:
            exif = Image.Exif()
            exif[274] = 6
            image.save(encoded, "JPEG", exif=exif)
        finally:
            image.close()
        payload = encoded.getvalue()
        UploadItem.objects.filter(pk=self.item_id).update(expected_size=len(payload))
        self.storage.add(self.item.incoming_key, payload)

        photo = self.confirm_success()

        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            ("preview_first_v1", "preview_required"),
        )
        preview = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        self.assertEqual(preview.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(preview.current_job.contract_version, 2)
        self.assertEqual(preview.current_job.processor_version, 1)
        self.assertEqual(
            preview.current_job.input_fingerprint["pixel_width"],
            20,
        )
        self.assertEqual(preview.current_job.input_fingerprint["pixel_height"], 40)
        self.assertEqual(face.status, PhotoProcessingState.Status.NOT_REQUESTED)
        self.assertIsNone(face.current_job)
        self.assertEqual(self.storage.geometry_read_atomic_states, [False])

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_enabled_paid_confirmation_persists_the_watermarked_pair(self) -> None:
        self.set_preview_payload()
        Event.objects.filter(pk=self.event.pk).update(
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        with override_feature_flags({"paid-watermarked-previews": FEATURE_FLAG_ON}):
            photo = self.confirm_success()

        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            ("preview_first_watermarked_v1", "watermarked_preview_required"),
        )

    @override_settings(
        PHOTO_PROCESSING_PREVIEW_ENABLED=True,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    @modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
    def test_admin_edit_commits_before_first_photo_policy_is_selected(self) -> None:
        self.set_preview_payload()
        get_user_model().objects.create_superuser(
            "race-admin", "race-admin@example.com", "password"
        )
        admin_holds_event_lock = ThreadEvent()
        allow_admin_commit = ThreadEvent()
        confirmation_attempted_event_lock = ThreadEvent()
        photo_created = ThreadEvent()
        from picflow.admin import EventAdmin

        original_save_model = EventAdmin.save_model
        original_select_for_update = Event.objects.select_for_update
        original_photo_create = Photo.objects.create

        def pause_admin_save(admin, request, obj, form, change):
            admin_holds_event_lock.set()
            if not allow_admin_commit.wait(timeout=5):
                raise AssertionError("test did not allow the admin edit to commit")
            return original_save_model(admin, request, obj, form, change)

        def observe_event_lock(*args, **kwargs):
            if current_thread().name.startswith("confirmation"):
                confirmation_attempted_event_lock.set()
            return original_select_for_update(*args, **kwargs)

        def observe_photo_create(*args, **kwargs):
            photo = original_photo_create(*args, **kwargs)
            if current_thread().name.startswith("confirmation"):
                photo_created.set()
            return photo

        def confirm_in_thread() -> Photo | None:
            close_old_connections()
            try:
                return self.confirm()
            finally:
                close_old_connections()

        with (
            override_feature_flags({"paid-watermarked-previews": FEATURE_FLAG_ON}),
            patch.object(EventAdmin, "save_model", pause_admin_save),
            patch.object(Event.objects, "select_for_update", side_effect=observe_event_lock),
            patch.object(Photo.objects, "create", side_effect=observe_photo_create),
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin-edit") as admin_executor,
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="confirmation"
            ) as confirm_executor,
        ):
            admin_future = admin_executor.submit(
                self.change_event_access_in_admin,
                access_type=cast(str, Event.AccessType.PAID),
            )
            self.assertTrue(admin_holds_event_lock.wait(timeout=5))
            confirm_future = confirm_executor.submit(confirm_in_thread)
            self.assertTrue(confirmation_attempted_event_lock.wait(timeout=5))
            try:
                self.assertFalse(photo_created.is_set())
            finally:
                allow_admin_commit.set()
            admin_response = admin_future.result(timeout=5)
            photo = confirm_future.result(timeout=5)

        self.assertEqual(admin_response.status_code, 302)
        assert photo is not None
        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            ("preview_first_watermarked_v1", "watermarked_preview_required"),
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.access_type, Event.AccessType.PAID)

    @override_settings(
        PHOTO_PROCESSING_PREVIEW_ENABLED=True,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    @modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
    def test_first_photo_commit_makes_concurrent_admin_edit_fail_validation(self) -> None:
        self.set_preview_payload()
        get_user_model().objects.create_superuser(
            "race-admin", "race-admin@example.com", "password"
        )
        photo_created = ThreadEvent()
        allow_photo_commit = ThreadEvent()
        admin_attempted_event_lock = ThreadEvent()
        original_photo_create = Photo.objects.create
        original_select_for_update = Event.objects.select_for_update

        def pause_photo_create(*args, **kwargs):
            photo = original_photo_create(*args, **kwargs)
            if current_thread().name.startswith("confirmation"):
                photo_created.set()
                if not allow_photo_commit.wait(timeout=5):
                    raise AssertionError("test did not allow confirmation to commit")
            return photo

        def observe_event_lock(*args, **kwargs):
            if current_thread().name.startswith("admin-edit"):
                admin_attempted_event_lock.set()
            return original_select_for_update(*args, **kwargs)

        def confirm_in_thread() -> Photo | None:
            close_old_connections()
            try:
                return self.confirm()
            finally:
                close_old_connections()

        with (
            patch.object(Photo.objects, "create", side_effect=pause_photo_create),
            patch.object(Event.objects, "select_for_update", side_effect=observe_event_lock),
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="confirmation"
            ) as confirm_executor,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin-edit") as admin_executor,
        ):
            confirm_future = confirm_executor.submit(confirm_in_thread)
            self.assertTrue(photo_created.wait(timeout=5))
            admin_future = admin_executor.submit(
                self.change_event_access_in_admin,
                access_type=cast(str, Event.AccessType.PAID),
            )
            self.assertTrue(admin_attempted_event_lock.wait(timeout=5))
            try:
                self.assertFalse(admin_future.done())
            finally:
                allow_photo_commit.set()
            photo = confirm_future.result(timeout=5)
            admin_response = admin_future.result(timeout=5)

        assert photo is not None
        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            ("preview_first_v1", "preview_required"),
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn(
            "Access type cannot be changed after the event has photos.",
            admin_response.context["adminform"].form.errors["access_type"],
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.access_type, Event.AccessType.FREE)

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_checkpoint_change_after_geometry_inspection_prevents_preview_first_persistence(
        self,
    ) -> None:
        from io import BytesIO

        encoded = BytesIO()
        image = Image.new("RGB", (40, 20), "white")
        try:
            image.save(encoded, "JPEG")
        finally:
            image.close()
        payload = encoded.getvalue()
        UploadItem.objects.filter(pk=self.item_id).update(expected_size=len(payload))
        self.storage.add(self.item.incoming_key, payload)

        def change_checkpoint(checkpoint: str) -> None:
            if checkpoint == "after_preview_geometry":
                UploadItem.objects.filter(pk=self.item_id).update(
                    verified_source_etag="newer-checkpoint"
                )

        self.assertIsNone(self.confirm(change_checkpoint))
        self.assertFalse(Photo.objects.filter(pk=self.item_id.hex).exists())

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_preview_geometry_rejects_images_over_the_shared_pixel_cap(self) -> None:
        from io import BytesIO

        encoded = BytesIO()
        image = Image.new("RGB", (40, 20), "white")
        try:
            image.save(encoded, "JPEG")
        finally:
            image.close()
        payload = encoded.getvalue()
        UploadItem.objects.filter(pk=self.item_id).update(expected_size=len(payload))
        self.storage.add(self.item.incoming_key, payload)
        worker = GENERATE_PREVIEW_CONFIGURATION["worker"]
        assert isinstance(worker, dict)

        with patch.dict(worker, {"max_pixels": 100}):
            self.assertIsNone(self.confirm())

        item = UploadItem.objects.get(pk=self.item_id)
        self.assertEqual(item.status, UploadItem.Status.FAILED)
        self.assertEqual(item.error_code, "invalid_jpeg")
        self.assertFalse(Photo.objects.filter(pk=self.item_id.hex).exists())

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_pillow_decompression_bomb_is_a_sanitized_terminal_invalid_jpeg(self) -> None:
        from io import BytesIO

        encoded = BytesIO()
        image = Image.new("RGB", (40, 20), "white")
        try:
            image.save(encoded, "JPEG")
        finally:
            image.close()
        payload = encoded.getvalue()
        UploadItem.objects.filter(pk=self.item_id).update(expected_size=len(payload))
        self.storage.add(self.item.incoming_key, payload)

        with patch.object(Image, "MAX_IMAGE_PIXELS", 1):
            self.assertIsNone(self.confirm())

        item = UploadItem.objects.get(pk=self.item_id)
        self.assertEqual(item.status, UploadItem.Status.FAILED)
        self.assertEqual(item.error_code, "invalid_jpeg")
        self.assertEqual(item.sanitized_error_message, "The uploaded file is not a valid JPEG.")
        self.assertNotIn("bomb", item.sanitized_error_message.lower())
        self.assertFalse(Photo.objects.filter(pk=self.item_id.hex).exists())

    def test_jpeg_signatures_metadata_and_final_identity_are_required(self) -> None:
        cases = [
            (b"NO" + self.jpeg[2:], None, "invalid_jpeg"),
            (self.jpeg[:-2] + b"NO", None, "invalid_jpeg"),
            (self.jpeg + b"x", None, "size_mismatch"),
            (self.jpeg, "application/octet-stream", "content_type_mismatch"),
        ]
        for content, content_type, code in cases:
            with self.subTest(code=code):
                self.storage.objects[self.item.incoming_key] = (
                    content,
                    '"source-etag"',
                    content_type or "image/jpeg",
                )
                self.confirm()
                item = UploadItem.objects.get(id=self.item_id)
                self.assertEqual(item.status, UploadItem.Status.FAILED)
                self.assertEqual(item.error_code, code)
                self.assertNotIn("source-etag", item.sanitized_error_message)
                self.assertEqual(Photo.objects.count(), 0)
                UploadItem.objects.filter(id=self.item_id).update(
                    status=UploadItem.Status.AUTHORIZED,
                    error_code="",
                    sanitized_error_message="",
                    completed_at=None,
                    verified_source_etag=None,
                )

    def test_too_short_jpeg_fails_before_any_range_read(self) -> None:
        for size in (1, 2, 3):
            with self.subTest(size=size):
                content = b"x" * size
                UploadItem.objects.filter(id=self.item_id).update(
                    expected_size=size,
                    status=UploadItem.Status.AUTHORIZED,
                    error_code="",
                    sanitized_error_message="",
                    completed_at=None,
                    verified_source_etag=None,
                )
                self.storage.objects[self.item.incoming_key] = (
                    content,
                    '"short"',
                    "image/jpeg",
                )
                self.storage.calls.clear()

                self.confirm()

                item = UploadItem.objects.get(id=self.item_id)
                self.assertEqual(item.status, UploadItem.Status.FAILED)
                self.assertEqual(item.error_code, "invalid_jpeg")
                self.assertNotIn("short", item.sanitized_error_message)
                self.assertFalse(any(call[0] == "read" for call in self.storage.calls))

    def test_etag_change_and_storage_failure_are_retryable_and_sanitized(self) -> None:
        for failure in (ObjectChanged(), StorageUnavailable()):
            with self.subTest(failure=type(failure).__name__):
                self.storage.failures["read"] = failure
                self.confirm()
                item = UploadItem.objects.get(id=self.item_id)
                self.assertEqual(item.status, UploadItem.Status.AUTHORIZED)
                self.assertTrue(item.error_code in {"object_changed", "storage_unavailable"})
                self.assertNotIn("secret", item.sanitized_error_message)
                self.assertEqual(Photo.objects.count(), 0)

    def test_copy_failure_is_retryable_but_final_mismatch_is_terminal(self) -> None:
        for failure, code in (
            (StorageUnavailable(), "storage_unavailable"),
            (ObjectChanged(), "object_changed"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.storage.failures["promote"] = failure
                self.confirm()
                item = UploadItem.objects.get(id=self.item_id)
                self.assertEqual(item.status, UploadItem.Status.AUTHORIZED)
                self.assertEqual(item.error_code, code)

        self.storage.final_after_promote = (self.jpeg, '"wrong-final"', "image/jpeg")
        self.confirm()
        item.refresh_from_db()
        self.assertEqual(item.status, UploadItem.Status.FAILED)
        self.assertEqual(item.error_code, "promotion_conflict")

    def test_incoming_delete_failure_does_not_undo_commit(self) -> None:
        self.storage.delete_failure = StorageUnavailable()
        photo = self.confirm_success()
        self.assert_completed_once(photo)
        self.assertIn(self.item.incoming_key, self.storage.objects)

    def test_cross_user_access_is_hidden_before_storage_access(self) -> None:
        with self.assertRaises(UploadBatch.DoesNotExist):
            confirm_upload_item(
                uploader=self.other,
                batch_id=self.batch_id,
                item_id=self.item_id,
                storage=self.storage,
            )
        self.assertEqual(self.storage.calls, [])

    def test_final_is_adopted_only_with_persisted_quote_free_checkpoint(self) -> None:
        self.storage.add(self.item.final_key, self.jpeg)
        self.confirm()
        item = UploadItem.objects.get(id=self.item_id)
        self.assertEqual(item.status, UploadItem.Status.FAILED)
        self.assertEqual(item.error_code, "promotion_conflict")
        self.assertIsNone(item.verified_source_etag)

        UploadItem.objects.filter(id=self.item_id).update(
            status=UploadItem.Status.AUTHORIZED,
            error_code="",
            sanitized_error_message="",
            completed_at=None,
            verified_source_etag="source-etag",
        )
        photo = self.confirm_success()
        self.assert_completed_once(photo)
        self.assertEqual(sum(call[0] == "promote" for call in self.storage.calls), 0)

    def test_recovery_rejects_final_with_wrong_etag_size_or_type(self) -> None:
        UploadItem.objects.filter(id=self.item_id).update(verified_source_etag="source-etag")
        cases = [
            (self.jpeg, '"wrong"', "image/jpeg"),
            (self.jpeg + b"x", '"source-etag"', "image/jpeg"),
            (self.jpeg, '"source-etag"', "image/png"),
        ]
        for content, etag, content_type in cases:
            with self.subTest(etag=etag, size=len(content), content_type=content_type):
                self.storage.objects[self.item.final_key] = (content, etag, content_type)
                self.confirm()
                item = UploadItem.objects.get(id=self.item_id)
                self.assertEqual(item.status, UploadItem.Status.FAILED)
                self.assertEqual(item.error_code, "promotion_conflict")
                self.assertEqual(Photo.objects.count(), 0)
                UploadItem.objects.filter(id=self.item_id).update(
                    status=UploadItem.Status.AUTHORIZED,
                    error_code="",
                    sanitized_error_message="",
                    completed_at=None,
                )

    def test_database_failure_after_copy_recovers_same_final(self) -> None:
        with patch(
            "ingestion.services.confirmation.Photo.objects.create", side_effect=IntegrityError
        ):
            with self.assertRaises(IntegrityError):
                self.confirm()
        self.assertIn(self.item.final_key, self.storage.objects)
        self.assertEqual(Photo.objects.count(), 0)
        photo = self.confirm_success()
        self.assert_completed_once(photo)
        self.assertEqual(sum(call[0] == "promote" for call in self.storage.calls), 1)

    def test_stale_confirmation_does_not_fail_a_newer_source_checkpoint(self) -> None:
        first_checkpoint_committed = ThreadEvent()
        allow_first_to_continue = ThreadEvent()

        def pause_first(checkpoint: str) -> None:
            if checkpoint == "after_source_checkpoint":
                first_checkpoint_committed.set()
                self.assertTrue(allow_first_to_continue.wait(timeout=5))

        def run_first() -> Photo | None:
            close_old_connections()
            try:
                return confirm_upload_item(
                    uploader=self.user,
                    batch_id=self.batch_id,
                    item_id=self.item_id,
                    storage=self.storage,
                    failpoint=pause_first,
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            stale_request = executor.submit(run_first)
            self.assertTrue(first_checkpoint_committed.wait(timeout=5))
            self.storage.add(self.item.incoming_key, self.jpeg, '"newer-etag"')

            def stop_after_new_checkpoint(checkpoint: str) -> None:
                if checkpoint == "after_source_checkpoint":
                    raise SimulatedCrash(checkpoint)

            with self.assertRaises(SimulatedCrash):
                self.confirm(stop_after_new_checkpoint)
            allow_first_to_continue.set()
            self.assertIsNone(stale_request.result(timeout=5))

        item = UploadItem.objects.get(id=self.item_id)
        self.assertEqual(item.status, UploadItem.Status.AUTHORIZED)
        self.assertEqual(item.verified_source_etag, "newer-etag")
        self.assertEqual(item.error_code, "object_changed")
        photo = self.confirm_success()
        self.assert_completed_once(photo)

    def test_crash_checkpoints_converge_on_retry(self) -> None:
        checkpoints = (
            "after_source_checkpoint",
            "after_copy",
            "after_final_head",
            "after_photo_commit",
            "before_incoming_delete",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                fired = False

                def crash(current: str, target: str = checkpoint) -> None:
                    nonlocal fired
                    if not fired and current == target:
                        fired = True
                        raise SimulatedCrash(target)

                with self.assertRaises(SimulatedCrash):
                    self.confirm(crash)
                photo = self.confirm_success()
                self.assert_completed_once(photo)
                if checkpoint in {"after_photo_commit", "before_incoming_delete"}:
                    self.assertNotIn(self.item.incoming_key, self.storage.objects)
                UploadItem.objects.filter(id=self.item_id).update(
                    photo=None,
                    status=UploadItem.Status.AUTHORIZED,
                    error_code="",
                    sanitized_error_message="",
                    completed_at=None,
                    verified_source_etag=None,
                )
                PhotoProcessingState.objects.all().delete()
                ProcessingAttempt.objects.all().delete()
                ProcessingJob.objects.all().delete()
                EventProcessingRun.objects.all().delete()
                Photo.objects.all().delete()
                UploadBatch.objects.filter(id=self.batch_id).update(
                    status=UploadBatch.Status.UPLOADING, completed_at=None
                )
                self.storage.objects.clear()
                self.storage.add(self.item.incoming_key, self.jpeg)
                self.storage.calls.clear()
