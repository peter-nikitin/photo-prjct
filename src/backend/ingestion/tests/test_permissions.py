from __future__ import annotations

import json
from datetime import date
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from ingestion.models import UploadBatch, UploadItem
from picflow.models import Event


@override_settings(PHOTO_UPLOAD_ENABLED=True)
class UploadPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        users = get_user_model().objects
        cls.plain = users.create_user(username="plain", password="pass")
        cls.photographer = users.create_user(username="photographer", password="pass")
        cls.staff = users.create_user(username="staff", password="pass", is_staff=True)
        cls.staff_photographer = users.create_user(
            username="staff-photo", password="pass", is_staff=True
        )
        cls.superuser = users.create_superuser(username="root", password="pass")
        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        cls.photographer.user_permissions.add(permission)
        cls.staff_photographer.user_permissions.add(permission)
        cls.event = Event.objects.create(
            name="Draft race",
            slug="draft-race",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 7, 13),
            city="Moscow",
        )

    def endpoints(self) -> list[tuple[str, str]]:
        batch = uuid4()
        item = uuid4()
        return [
            ("get", reverse("upload_page")),
            ("post", reverse("upload_batch_create")),
            ("post", reverse("upload_items_register", args=[batch])),
            ("post", reverse("upload_item_authorize", args=[batch, item])),
            ("post", reverse("upload_item_confirm", args=[batch, item])),
            ("post", reverse("upload_item_failed", args=[batch, item])),
            ("post", reverse("upload_batch_finalize", args=[batch])),
        ]

    @staticmethod
    def call(client: Client, method: str, url: str):
        if method == "get":
            return client.get(url)
        return client.post(url, data="{}", content_type="application/json")

    def test_anonymous_users_are_redirected_to_photographer_login(self) -> None:
        for method, url in self.endpoints():
            response = self.call(self.client, method, url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse("photographer_login")))

    def test_authenticated_users_without_permission_are_forbidden(self) -> None:
        for user in (self.plain, self.staff):
            client = Client()
            client.force_login(user)
            for method, url in self.endpoints():
                response = self.call(client, method, url)
                self.assertEqual(response.status_code, 403)

        client = Client()
        client.force_login(self.plain)
        response = client.post(
            reverse("upload_batch_create"), data="{}", content_type="application/json"
        )
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "permission_denied",
                    "message": "You do not have permission to upload photos.",
                    "fields": {},
                }
            },
        )

    def test_photographers_staff_photographers_and_superusers_are_authorized(self) -> None:
        for user in (self.photographer, self.staff_photographer, self.superuser):
            client = Client()
            client.force_login(user)
            response = client.get(reverse("upload_page"))
            self.assertEqual(response.status_code, 200)

            response = client.post(
                reverse("upload_batch_create"),
                json.dumps({"event_id": self.event.id, "expected_item_count": 1}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)

            for _, url in self.endpoints()[2:]:
                self.assertNotEqual(
                    client.post(url, "{}", content_type="application/json").status_code,
                    403,
                )

    def test_other_users_batch_and_item_are_hidden_as_not_found(self) -> None:
        batch = UploadBatch.objects.create(
            uploader=self.photographer, event=self.event, expected_item_count=1
        )
        item = UploadItem.objects.create(
            batch=batch,
            client_item_id=uuid4(),
            original_filename="one.jpg",
            declared_content_type="image/jpeg",
            expected_size=4,
            incoming_key=f"incoming/{batch.id}/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
        )
        foreign_batch = UploadBatch.objects.create(
            uploader=self.staff_photographer, event=self.event, expected_item_count=1
        )
        client = Client()
        client.force_login(self.staff_photographer)

        urls = [
            reverse("upload_items_register", args=[batch.id]),
            reverse("upload_item_authorize", args=[batch.id, item.id]),
            reverse("upload_item_confirm", args=[batch.id, item.id]),
            reverse("upload_item_failed", args=[batch.id, item.id]),
            reverse("upload_batch_finalize", args=[batch.id]),
            reverse("upload_item_authorize", args=[foreign_batch.id, item.id]),
        ]
        for url in urls:
            response = client.post(url, "{}", content_type="application/json")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")

    @override_settings(PHOTO_UPLOAD_ENABLED=False)
    def test_disabled_feature_hides_upload_routes_but_not_auth_routes(self) -> None:
        self.client.force_login(self.photographer)
        for method, url in self.endpoints():
            response = self.call(self.client, method, url)
            self.assertEqual(response.status_code, 404)

        self.assertNotEqual(self.client.get(reverse("photographer_login")).status_code, 404)
        self.assertNotEqual(self.client.post(reverse("photographer_logout")).status_code, 404)

    def test_admin_is_read_only_and_does_not_expose_object_keys(self) -> None:
        batch_admin = admin.site._registry[UploadBatch]
        item_admin = admin.site._registry[UploadItem]
        request = type("Request", (), {"user": self.superuser})()

        for model_admin in (batch_admin, item_admin):
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
        item_fields = item_admin.get_fields(request)
        self.assertNotIn("incoming_key", item_fields)
        self.assertNotIn("final_key", item_fields)
