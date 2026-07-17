from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ingestion.models import UploadItem
from ingestion.storage import StorageUnavailable, UploadGrant
from picflow.models import Event, Photo


@override_settings(PHOTO_UPLOAD_ENABLED=True)
class UploadViewTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_user(username="photo", password="pass")
        cls.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")
        )
        cls.event = Event.objects.create(
            name="Race",
            slug="race",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 7, 13),
            city="Moscow",
        )

    def setUp(self) -> None:
        self.client.force_login(self.user)

    def create_batch(self, expected: int = 1) -> tuple[UUID, dict[str, Any]]:
        response = self.client.post(
            reverse("upload_batch_create"),
            json.dumps({"event_id": self.event.id, "expected_item_count": expected}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        return UUID(payload["batch"]["id"]), payload

    def register_item(self, batch_id: UUID, client_item_id: UUID | None = None):
        client_item_id = client_item_id or uuid4()
        response = self.client.post(
            reverse("upload_items_register", args=[batch_id]),
            json.dumps(
                {
                    "items": [
                        {
                            "client_item_id": str(client_item_id),
                            "filename": "race.jpg",
                            "content_type": "image/jpeg",
                            "size": 4,
                        }
                    ]
                }
            ),
            content_type="application/json",
        )
        return client_item_id, response

    def test_named_routes_match_the_public_contract(self) -> None:
        batch = uuid4()
        item = uuid4()
        self.assertEqual(reverse("photographer_login"), "/photographer/login/")
        self.assertEqual(reverse("photographer_logout"), "/photographer/logout/")
        self.assertEqual(reverse("upload_page"), "/photographer/uploads/")
        self.assertEqual(reverse("upload_batch_create"), "/photographer/uploads/batches/")
        self.assertEqual(
            reverse("upload_items_register", args=[batch]),
            f"/photographer/uploads/{batch}/items/",
        )
        self.assertEqual(
            reverse("upload_item_authorize", args=[batch, item]),
            f"/photographer/uploads/{batch}/items/{item}/authorize/",
        )
        self.assertEqual(
            reverse("upload_item_retry", args=[batch, item]),
            f"/photographer/uploads/{batch}/items/{item}/retry/",
        )
        self.assertEqual(
            reverse("upload_item_confirm", args=[batch, item]),
            f"/photographer/uploads/{batch}/items/{item}/confirm/",
        )
        self.assertEqual(
            reverse("upload_item_failed", args=[batch, item]),
            f"/photographer/uploads/{batch}/items/{item}/failed/",
        )
        self.assertEqual(
            reverse("upload_batch_finalize", args=[batch]),
            f"/photographer/uploads/{batch}/finalize/",
        )

    def test_batch_create_returns_exact_public_shape_and_accepts_draft_event(self) -> None:
        _, payload = self.create_batch(expected=42)
        self.assertEqual(
            payload,
            {
                "batch": {
                    "id": payload["batch"]["id"],
                    "status": "created",
                    "expected_item_count": 42,
                }
            },
        )

    def test_registration_returns_201_then_idempotent_200_without_keys(self) -> None:
        batch_id, _ = self.create_batch()
        client_item_id = uuid4()
        _, created = self.register_item(batch_id, client_item_id)
        _, replay = self.register_item(batch_id, client_item_id)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(created.json(), replay.json())
        item = created.json()["items"][0]
        self.assertEqual(
            item,
            {"id": item["id"], "client_item_id": str(client_item_id), "status": "pending"},
        )
        serialized = json.dumps(created.json())
        self.assertNotIn("incoming", serialized)
        self.assertNotIn("originals", serialized)

    @patch("ingestion.views.PrivateUploadStorage")
    def test_authorize_is_only_response_with_signed_form(self, storage_class) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]
        expires_at = timezone.now() + timedelta(minutes=10)
        storage_class.return_value.create_presigned_post.return_value = UploadGrant(
            "https://storage.example/upload",
            {"key": "opaque", "policy": "signed"},
            expires_at,
        )

        response = self.client.post(
            reverse("upload_item_authorize", args=[batch_id, item_id]),
            json.dumps({"reason": "data_attempt"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["item"], {"id": item_id, "status": "authorized", "attempt": 1})
        self.assertEqual(payload["grant"]["url"], "https://storage.example/upload")
        self.assertEqual(payload["grant"]["fields"], {"key": "opaque", "policy": "signed"})
        self.assertEqual(payload["grant"]["expires_at"], expires_at.isoformat())

    @patch("ingestion.views.confirm_upload_item")
    @patch("ingestion.views.PrivateUploadStorage")
    def test_confirm_returns_uploaded_photo_without_private_metadata(
        self, storage_class, confirm
    ) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = UUID(registered.json()["items"][0]["id"])
        item = UploadItem.objects.get(id=item_id)
        item.status = UploadItem.Status.AUTHORIZED
        item.upload_attempts = 1
        item.authorization_expires_at = timezone.now() + timedelta(minutes=10)
        item.save()
        photo = Photo.objects.create(
            id=item.id.hex,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=item.final_key,
            original_filename=item.original_filename,
            original_size=item.expected_size,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        item.photo = photo
        item.status = UploadItem.Status.UPLOADED
        item.authorization_expires_at = None
        item.completed_at = timezone.now()
        item.save()
        confirm.return_value = photo

        response = self.client.post(
            reverse("upload_item_confirm", args=[batch_id, item_id]),
            "{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"item": {"id": str(item_id), "status": "uploaded", "photo_id": photo.id}},
        )
        self.assertNotIn(item.final_key, response.content.decode())
        storage_class.assert_called_once_with()

    @patch("ingestion.views.PrivateUploadStorage")
    def test_manual_retry_returns_public_item_and_transient_signed_form(
        self, storage_class
    ) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]
        self.client.post(
            reverse("upload_item_failed", args=[batch_id, item_id]),
            json.dumps({"code": "transfer_retries_exhausted"}),
            content_type="application/json",
        )
        expires_at = timezone.now() + timedelta(minutes=10)
        storage_class.return_value.create_presigned_post.return_value = UploadGrant(
            "https://storage.example/retry",
            {"key": "opaque", "policy": "fresh"},
            expires_at,
        )

        response = self.client.post(
            reverse("upload_item_retry", args=[batch_id, item_id]),
            "{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "item": {"id": item_id, "status": "authorized", "attempt": 1},
                "grant": {
                    "url": "https://storage.example/retry",
                    "fields": {"key": "opaque", "policy": "fresh"},
                    "expires_at": expires_at.isoformat(),
                },
            },
        )
        self.assertNotIn("incoming/", response.content.decode())
        self.assertNotIn("originals/", response.content.decode())

    @patch("ingestion.views.PrivateUploadStorage")
    def test_manual_retry_requires_exact_empty_object_and_failed_state(self, storage_class) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]

        extra = self.client.post(
            reverse("upload_item_retry", args=[batch_id, item_id]),
            json.dumps({"reason": "data_attempt"}),
            content_type="application/json",
        )
        pending = self.client.post(
            reverse("upload_item_retry", args=[batch_id, item_id]),
            "{}",
            content_type="application/json",
        )

        self.assertEqual(extra.status_code, 400)
        self.assertEqual(extra.json()["error"]["code"], "validation_error")
        self.assertEqual(pending.status_code, 409)
        self.assertEqual(pending.json()["error"]["code"], "item_not_failed")
        storage_class.return_value.create_presigned_post.assert_not_called()

    @patch("ingestion.views.PrivateUploadStorage")
    def test_manual_retry_storage_failure_is_sanitized_503(self, storage_class) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]
        self.client.post(
            reverse("upload_item_failed", args=[batch_id, item_id]),
            json.dumps({"code": "transfer_cancelled"}),
            content_type="application/json",
        )
        storage_class.return_value.create_presigned_post.side_effect = StorageUnavailable()

        response = self.client.post(
            reverse("upload_item_retry", args=[batch_id, item_id]),
            "{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
        self.assertNotIn("bucket", response.content.decode())

    def test_failed_and_finalize_return_exact_shapes(self) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]

        failed = self.client.post(
            reverse("upload_item_failed", args=[batch_id, item_id]),
            json.dumps({"code": "transfer_cancelled"}),
            content_type="application/json",
        )
        finalized = self.client.post(
            reverse("upload_batch_finalize", args=[batch_id]),
            "{}",
            content_type="application/json",
        )

        self.assertEqual(failed.status_code, 200)
        self.assertEqual(
            failed.json(),
            {
                "item": {
                    "id": item_id,
                    "status": "failed",
                    "error_code": "transfer_cancelled",
                }
            },
        )
        self.assertEqual(finalized.status_code, 200)
        self.assertEqual(
            finalized.json(),
            {
                "batch": {
                    "id": str(batch_id),
                    "status": "failed",
                    "expected": 1,
                    "uploaded": 0,
                    "failed": 1,
                }
            },
        )

    def test_validation_and_conflict_errors_use_stable_envelope(self) -> None:
        invalid_json = self.client.post(
            reverse("upload_batch_create"), "{", content_type="application/json"
        )
        validation = self.client.post(
            reverse("upload_batch_create"),
            json.dumps({"event_id": self.event.id, "expected_item_count": 0}),
            content_type="application/json",
        )
        batch_id, _ = self.create_batch()
        conflict = self.client.post(
            reverse("upload_batch_finalize", args=[batch_id]),
            "{}",
            content_type="application/json",
        )

        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(invalid_json.json()["error"]["code"], "invalid_json")
        self.assertEqual(validation.status_code, 400)
        self.assertEqual(validation.json()["error"]["code"], "validation_error")
        self.assertIn("expected_item_count", validation.json()["error"]["fields"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "missing_registrations")
        for response in (invalid_json, validation, conflict):
            self.assertEqual(set(response.json()["error"]), {"code", "message", "fields"})

    def test_deeply_nested_json_returns_stable_invalid_json(self) -> None:
        body = '{"nested":' * 10_000 + "null" + "}" * 10_000

        response = self.client.post(
            reverse("upload_batch_create"), body, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "invalid_json",
                    "message": "Request body must be valid JSON.",
                    "fields": {},
                }
            },
        )

    @patch("ingestion.views.confirm_upload_item", return_value=None)
    @patch("ingestion.views.PrivateUploadStorage")
    def test_confirm_preserves_retryable_error_codes(self, storage_class, confirm_upload) -> None:
        cases = {
            "object_changed": "The uploaded object changed. Upload the file again.",
            "object_missing": "The uploaded object is not available yet.",
            "storage_unavailable": "Object storage is temporarily unavailable.",
        }
        for code, message in cases.items():
            with self.subTest(code=code):
                batch_id, _ = self.create_batch()
                _, registered = self.register_item(batch_id)
                item_id = registered.json()["items"][0]["id"]
                UploadItem.objects.filter(pk=item_id).update(
                    error_code=code,
                    sanitized_error_message=message,
                )

                response = self.client.post(
                    reverse("upload_item_confirm", args=[batch_id, item_id]),
                    "{}",
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json(),
                    {"error": {"code": code, "message": message, "fields": {}}},
                )
        self.assertEqual(confirm_upload.call_count, 3)
        self.assertEqual(storage_class.call_count, 3)

    @patch("ingestion.views.PrivateUploadStorage")
    def test_confirm_converges_when_reread_observes_concurrent_completion(
        self, storage_class
    ) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]

        def complete_concurrently(**kwargs):
            item = UploadItem.objects.get(pk=item_id)
            photo = Photo.objects.create(
                id=item.id.hex,
                event=self.event,
                src="",
                uploaded_by=self.user,
                original_key=item.final_key,
                original_filename=item.original_filename,
                original_size=item.expected_size,
                original_content_type=item.declared_content_type,
                uploaded_at=timezone.now(),
            )
            item.photo = photo
            item.status = UploadItem.Status.UPLOADED
            item.authorization_expires_at = None
            item.completed_at = timezone.now()
            item.save(
                update_fields=[
                    "photo",
                    "status",
                    "authorization_expires_at",
                    "completed_at",
                ]
            )
            return None

        with patch(
            "ingestion.views.confirm_upload_item", side_effect=complete_concurrently
        ) as confirm_upload:
            response = self.client.post(
                reverse("upload_item_confirm", args=[batch_id, item_id]),
                "{}",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "item": {
                    "id": item_id,
                    "status": "uploaded",
                    "photo_id": UUID(item_id).hex,
                }
            },
        )
        confirm_upload.assert_called_once()
        storage_class.assert_called_once_with()

    @patch("ingestion.views.PrivateUploadStorage")
    def test_storage_failures_are_sanitized_503(self, storage_class) -> None:
        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        item_id = registered.json()["items"][0]["id"]
        storage_class.return_value.create_presigned_post.side_effect = StorageUnavailable()

        response = self.client.post(
            reverse("upload_item_authorize", args=[batch_id, item_id]),
            json.dumps({"reason": "data_attempt"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "storage_unavailable",
                    "message": "Object storage is temporarily unavailable.",
                    "fields": {},
                }
            },
        )
        self.assertNotIn("bucket", response.content.decode())

    def test_control_endpoints_require_csrf(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(
            reverse("upload_batch_create"),
            json.dumps({"event_id": self.event.id, "expected_item_count": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

        batch_id, _ = self.create_batch()
        _, registered = self.register_item(batch_id)
        retry = csrf_client.post(
            reverse("upload_item_retry", args=[batch_id, registered.json()["items"][0]["id"]]),
            "{}",
            content_type="application/json",
        )
        self.assertEqual(retry.status_code, 403)
