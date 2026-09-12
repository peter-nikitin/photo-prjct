from itertools import count
from typing import cast
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from ingestion.models import UploadBatch, UploadItem
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingJob,
)

from picflow.tests.event_management_helpers import (
    event,
    private_photo,
    user_with_permissions,
)

ADMIN_PERMISSIONS = ("picflow.view_event", "picflow.view_photo")


@override_settings(
    ROOT_URLCONF="picflow.tests.event_management_urlconf",
    PHOTO_UPLOAD_ENABLED=True,
)
class EventManagementStatusTests(TestCase):
    def setUp(self) -> None:
        self.event = event()
        self.admin = user_with_permissions(
            "status-admin", staff=True, permissions=ADMIN_PERMISSIONS
        )
        self.uploader = user_with_permissions(
            "status-uploader", permissions=("ingestion.upload_photos",)
        )
        self.other = user_with_permissions("status-other", permissions=("ingestion.upload_photos",))
        self.url = reverse("event_management_status", args=[self.event.pk])
        self._identity = count(1)

    def batch(self, uploader, **overrides) -> UploadBatch:
        return UploadBatch.objects.create(
            event=self.event,
            uploader=uploader,
            expected_item_count=overrides.pop("expected_item_count", 1),
            **overrides,
        )

    def confirmed_item(self, batch: UploadBatch, photo) -> UploadItem:
        return UploadItem.objects.create(
            batch=batch,
            photo=photo,
            status=UploadItem.Status.UPLOADED,
            client_item_id=uuid4(),
            original_filename="must-not-leak.jpg",
            declared_content_type="image/jpeg",
            expected_size=4,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
            error_code="must-not-leak",
            sanitized_error_message="must-not-leak-detail",
        )

    def set_capture_status(self, photo, status: str) -> PhotoProcessingState:
        identity = next(self._identity)
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=1,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration={},
            configuration_hash=f"{identity:064x}",
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration={},
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=status,
        )
        state, _created = PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            defaults={
                "status": status,
                "current_run": run,
                "current_job": job,
                "current_attempt": None,
                "accepted_attempt": None,
            },
        )
        return state

    def assert_private(self, response) -> None:
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_admin_payload_includes_hidden_photo_and_event_wide_active_summary(self) -> None:
        hidden = private_photo(self.event, self.uploader, is_hidden=True)
        active = private_photo(self.event, self.uploader)
        self.set_capture_status(active, cast(str, PhotoProcessingState.Status.QUEUED))
        other_event_photo = private_photo(event("Other event"), self.uploader)

        self.client.force_login(self.admin)
        response = self.client.get(
            self.url,
            {
                "visibility": "hidden",
                "photo_id": [hidden.pk, active.pk, other_event_photo.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assert_private(response)
        payload = response.json()
        self.assertEqual(payload["admin"]["summary"]["total"], 2)
        self.assertTrue(payload["admin"]["summary"]["has_active_work"])
        self.assertEqual(payload["admin"]["filtered_result_count"], 1)
        self.assertTrue(payload["admin"]["result_list_changed"])
        self.assertEqual(
            {row["photo_id"] for row in payload["admin"]["photos"]},
            {hidden.pk, active.pk},
        )
        self.assertTrue(payload["has_active_work"])
        self.assertRegex(payload["server_timestamp"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertNotIn("batches", payload)
        for secret in (hidden.original_key, "must-not-leak", other_event_photo.pk):
            self.assertNotIn(secret, response.content.decode())

    def test_result_list_change_uses_strict_filters_page_and_displayed_order(self) -> None:
        first = private_photo(self.event, self.uploader)
        second = private_photo(self.event, self.uploader)
        displayed = sorted((first.pk, second.pk))
        self.client.force_login(self.admin)

        current = self.client.get(
            self.url,
            {"visibility": "visible", "photo_id": displayed},
        ).json()
        self.assertFalse(current["admin"]["result_list_changed"])

        second.is_hidden = True
        second.save(update_fields=["is_hidden"])
        changed = self.client.get(
            self.url,
            {"visibility": "visible", "photo_id": displayed},
        ).json()
        self.assertTrue(changed["admin"]["result_list_changed"])
        self.assertEqual(changed["admin"]["filtered_result_count"], 1)
        self.assertEqual(
            {row["photo_id"] for row in changed["admin"]["photos"]},
            {first.pk, second.pk},
        )

        invalid = self.client.get(self.url, {"visibility": "unknown"})
        self.assertEqual(invalid.status_code, 400)
        self.assert_private(invalid)

    def test_timezone_less_unavailable_event_returns_private_400_for_time_range(self) -> None:
        self.client.force_login(self.admin)

        response = self.client.get(
            self.url,
            {"from": f"{self.event.start_date.isoformat()}T12:00"},
        )

        self.assertEqual(response.status_code, 400)
        self.assert_private(response)
        self.assertEqual(response.json(), {"error": "invalid_filters"})

    def test_invalid_initial_filter_can_omit_result_scope_without_broadening_it(self) -> None:
        private_photo(self.event, self.uploader, is_hidden=True)
        self.client.force_login(self.admin)

        response = self.client.get(
            self.url,
            {"folder": "missing", "include_results": "0"},
        )

        self.assertEqual(response.status_code, 200)
        self.assert_private(response)
        payload = response.json()
        self.assertEqual(
            payload["capabilities"],
            {"can_inspect": True, "can_upload": False},
        )
        self.assertEqual(payload["admin"]["summary"]["total"], 1)
        self.assertNotIn("filtered_result_count", payload["admin"])
        self.assertNotIn("result_list_changed", payload["admin"])
        self.assertNotIn("photos", payload["admin"])

    def test_partial_role_changes_keep_remaining_scope_available(self) -> None:
        both = user_with_permissions(
            "status-both",
            staff=True,
            permissions=(*ADMIN_PERMISSIONS, "ingestion.upload_photos"),
        )
        batch = self.batch(both)
        self.client.force_login(both)

        with self.settings(PHOTO_UPLOAD_ENABLED=False):
            admin_only = self.client.get(self.url, {"batch_id": str(batch.pk)}).json()
        self.assertEqual(
            admin_only["capabilities"],
            {"can_inspect": True, "can_upload": False},
        )
        self.assertIn("admin", admin_only)
        self.assertNotIn("batches", admin_only)

        for permission_name in ADMIN_PERMISSIONS:
            app_label, codename = permission_name.split(".")
            both.user_permissions.remove(
                Permission.objects.get(
                    content_type__app_label=app_label,
                    codename=codename,
                )
            )
        upload_only = self.client.get(self.url, {"batch_id": str(batch.pk)}).json()
        self.assertEqual(
            upload_only["capabilities"],
            {"can_inspect": False, "can_upload": True},
        )
        self.assertNotIn("admin", upload_only)
        self.assertEqual([row["id"] for row in upload_only["batches"]], [str(batch.pk)])

    def test_uploader_receives_only_requested_owned_batch_summaries(self) -> None:
        active_photo = private_photo(self.event, self.uploader)
        self.set_capture_status(active_photo, cast(str, PhotoProcessingState.Status.RETRY_WAIT))
        own = self.batch(self.uploader, status=UploadBatch.Status.COMPLETED)
        self.confirmed_item(own, active_photo)
        foreign = self.batch(self.other, status=UploadBatch.Status.UPLOADING)
        self.confirmed_item(foreign, private_photo(self.event, self.other))

        self.client.force_login(self.uploader)
        response = self.client.get(
            self.url,
            {"batch_id": [str(own.pk), str(foreign.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("admin", payload)
        self.assertEqual([row["id"] for row in payload["batches"]], [str(own.pk)])
        self.assertEqual(payload["batches"][0]["processing"]["total"], 1)
        self.assertTrue(payload["batches"][0]["has_active_work"])
        self.assertTrue(payload["has_active_work"])
        for secret in (str(foreign.pk), "must-not-leak", active_photo.pk):
            self.assertNotIn(secret, response.content.decode())

    def test_requested_owned_event_batches_keep_updating_after_history_page_shifts(self) -> None:
        active = self.batch(self.uploader, status=UploadBatch.Status.UPLOADING)
        terminal = [self.batch(self.uploader, status=UploadBatch.Status.FAILED) for _ in range(17)]
        foreign_owner = self.batch(self.other, status=UploadBatch.Status.UPLOADING)
        foreign_event = UploadBatch.objects.create(
            event=event("Foreign status event"),
            uploader=self.uploader,
            expected_item_count=1,
            status=UploadBatch.Status.UPLOADING,
        )
        requested = [
            *(str(batch.pk) for batch in terminal),
            str(active.pk),
            str(foreign_owner.pk),
            str(foreign_event.pk),
        ]
        for _ in range(3):
            self.batch(self.uploader, status=UploadBatch.Status.FAILED)
        self.client.force_login(self.uploader)

        payload = self.client.get(self.url, {"batch_id": requested}).json()

        self.assertEqual(
            {row["id"] for row in payload["batches"]},
            {str(batch.pk) for batch in [*terminal, active]},
        )
        response_ids = repr(payload["batches"])
        self.assertNotIn(str(foreign_owner.pk), response_ids)
        self.assertNotIn(str(foreign_event.pk), response_ids)
        self.assertTrue(payload["has_active_work"])

    def test_terminal_errors_do_not_keep_polling_active(self) -> None:
        failed_photo = private_photo(self.event, self.uploader)
        self.set_capture_status(failed_photo, cast(str, PhotoProcessingState.Status.FAILED))
        batch = self.batch(self.uploader, status=UploadBatch.Status.FAILED)
        self.confirmed_item(batch, failed_photo)
        self.client.force_login(self.uploader)

        payload = self.client.get(self.url, {"batch_id": str(batch.pk)}).json()

        self.assertFalse(payload["batches"][0]["processing"]["has_active_work"])
        self.assertFalse(payload["batches"][0]["has_active_work"])
        self.assertFalse(payload["has_active_work"])

    def test_durable_confirmation_stops_transfer_polling_even_if_batch_status_lags(self) -> None:
        confirmed_photo = private_photo(self.event, self.uploader)
        batch = self.batch(self.uploader, status=UploadBatch.Status.UPLOADING)
        self.confirmed_item(batch, confirmed_photo)
        self.client.force_login(self.uploader)

        payload = self.client.get(self.url, {"batch_id": str(batch.pk)}).json()

        self.assertTrue(payload["batches"][0]["can_close"])
        self.assertFalse(payload["batches"][0]["has_active_work"])
        self.assertFalse(payload["has_active_work"])

    def test_status_rechecks_permissions_before_any_scoped_data(self) -> None:
        hidden = private_photo(self.event, self.uploader, is_hidden=True)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(self.url, {"photo_id": hidden.pk}).status_code,
            200,
        )
        for permission_name in ADMIN_PERMISSIONS:
            app_label, codename = permission_name.split(".")
            permission = Permission.objects.get(
                content_type__app_label=app_label, codename=codename
            )
            self.admin.user_permissions.remove(permission)

        response = self.client.get(self.url, {"photo_id": hidden.pk})

        self.assertEqual(response.status_code, 403)
        self.assert_private(response)
        self.assertNotIn(hidden.pk, response.content.decode())

    def test_anonymous_redirects_and_inactive_or_partial_roles_are_denied(self) -> None:
        anonymous = self.client.get(self.url)
        self.assertEqual(anonymous.status_code, 401)
        self.assert_private(anonymous)

        users = [
            (
                user_with_permissions("status-staff", staff=True),
                403,
            ),
            (
                user_with_permissions(
                    "status-partial", staff=True, permissions=(ADMIN_PERMISSIONS[0],)
                ),
                403,
            ),
        ]
        self.uploader.is_active = False
        self.uploader.save(update_fields=["is_active"])
        users.append((self.uploader, 401))
        for user, expected_status in users:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, expected_status)
                self.assert_private(response)

    def test_displayed_id_bounds_and_shapes_are_rejected(self) -> None:
        self.client.force_login(self.admin)
        cases = [
            {"photo_id": [f"photo-{index}" for index in range(101)]},
            {"photo_id": ""},
            {"photo_id": "x" * 256},
            {"batch_id": [str(uuid4()) for _ in range(21)]},
            {"batch_id": "not-a-uuid"},
        ]
        for query in cases:
            with self.subTest(query=query):
                response = self.client.get(self.url, query)
                self.assertEqual(response.status_code, 400)
                self.assert_private(response)

    def test_missing_event_and_non_get_are_private_errors(self) -> None:
        self.client.force_login(self.admin)
        responses = (
            self.client.get(reverse("event_management_status", args=[999999])),
            self.client.post(self.url),
        )
        for response in responses:
            self.assertIn(response.status_code, (404, 405))
            self.assert_private(response)
