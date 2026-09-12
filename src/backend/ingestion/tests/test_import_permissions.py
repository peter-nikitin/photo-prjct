from __future__ import annotations

import json
from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from feature_flags.models import FeatureFlag
from feature_flags.registry import YANDEX_DISK_IMPORT
from ingestion.models import ImportBatch
from picflow.models import Event, EventFolder


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    PHOTO_IMPORT_ENABLED=True,
    PHOTO_IMPORT_WORKER_TOKEN="dedicated-import-token",
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="photo-worker-token",
    PHOTO_IMPORT_MAX_JSON_BYTES=1024 * 1024,
)
class ImportPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        users = get_user_model().objects
        cls.owner = users.create_user(username="owner", password="password")
        cls.other = users.create_user(username="other", password="password")
        cls.staff = users.create_user(username="staff", password="password", is_staff=True)
        cls.superuser = users.create_superuser(
            username="superuser", password="password", email="admin@example.test"
        )
        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        cls.owner.user_permissions.add(permission)
        cls.other.user_permissions.add(permission)
        cls.event = Event.objects.create(
            name="Permission event",
            slug="permission-event",
            start_date="2026-09-01",
            end_date="2026-09-01",
            city="Moscow",
        )
        cls.other_event = Event.objects.create(
            name="Other event",
            slug="other-event",
            start_date="2026-09-02",
            end_date="2026-09-02",
            city="Moscow",
        )
        cls.foreign_folder = EventFolder.objects.create(event=cls.other_event, name="Foreign")

    def setUp(self) -> None:
        FeatureFlag.objects.create(
            key=YANDEX_DISK_IMPORT.key,
            description=YANDEX_DISK_IMPORT.description,
            state=FeatureFlag.State.ON,
        )

    def post_json(self, url: str, data: Mapping[str, object], **extra: object):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
            **extra,
        )

    def create_as_owner(self) -> ImportBatch:
        self.client.force_login(self.owner)
        response = self.post_json(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": None,
                "source_url": "https://yadi.sk/d/public-key",
                "submission_key": "permission-import",
            },
        )
        return ImportBatch.objects.get(pk=response.json()["batch"]["id"])

    def test_staff_without_upload_permission_cannot_create_or_read_imports(self) -> None:
        batch = self.create_as_owner()
        self.client.force_login(self.staff)

        create = self.post_json(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": None,
                "source_url": "https://disk.yandex.ru/d/key",
                "submission_key": "staff-import",
            },
        )
        detail = self.client.get(reverse("import_detail", args=[batch.pk]))

        self.assertEqual(create.status_code, 403)
        self.assertEqual(detail.status_code, 403)

    def test_cross_owner_detail_items_and_retry_are_not_disclosed(self) -> None:
        batch = self.create_as_owner()
        self.client.force_login(self.other)

        detail = self.client.get(reverse("import_detail", args=[batch.pk]))
        items = self.client.get(reverse("import_items", args=[batch.pk]))
        retry = self.post_json(reverse("import_retry", args=[batch.pk]), {"contract_version": 1})

        for response in (detail, items, retry):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_superuser_can_read_and_retry_an_owned_failed_import(self) -> None:
        batch = self.create_as_owner()
        batch.status = ImportBatch.Status.FAILED
        batch.error_code = "source_unavailable"
        batch.save(update_fields=["status", "error_code"])
        self.client.force_login(self.superuser)

        detail = self.client.get(reverse("import_detail", args=[batch.pk]))
        retry = self.post_json(reverse("import_retry", args=[batch.pk]), {"contract_version": 1})

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.QUEUED)

    def test_cross_event_folder_is_rejected_without_creating_import(self) -> None:
        self.client.force_login(self.owner)
        response = self.post_json(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": self.foreign_folder.pk,
                "source_url": "https://disk.yandex.ru/d/key",
                "submission_key": "cross-event",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ImportBatch.objects.filter(submission_key="cross-event").exists())

    def test_gate_and_capability_block_mutations_but_not_owned_progress_reads(self) -> None:
        batch = self.create_as_owner()
        flag = FeatureFlag.objects.get(key=YANDEX_DISK_IMPORT.key)
        flag.state = FeatureFlag.State.OFF
        flag.save(update_fields=["state"])

        gate_blocked = self.post_json(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": None,
                "source_url": "https://disk.yandex.ru/d/other-key",
                "submission_key": "gate-blocked",
            },
        )
        read_with_gate_off = self.client.get(reverse("import_detail", args=[batch.pk]))
        with override_settings(PHOTO_IMPORT_ENABLED=False):
            capability_blocked = self.post_json(
                reverse("import_retry", args=[batch.pk]), {"contract_version": 1}
            )
            read_with_capability_off = self.client.get(reverse("import_detail", args=[batch.pk]))

        self.assertEqual(gate_blocked.status_code, 409)
        self.assertEqual(gate_blocked.json()["error"]["code"], "feature_paused")
        self.assertEqual(capability_blocked.status_code, 404)
        self.assertEqual(read_with_gate_off.status_code, 200)
        self.assertEqual(read_with_capability_off.status_code, 200)

    def test_worker_requires_only_dedicated_token_and_handles_malformed_headers(self) -> None:
        url = reverse("import_worker_claim")
        body = {"contract_version": 1, "lease_seconds": 120}
        missing = self.post_json(url, body)
        photo_worker = self.post_json(url, body, HTTP_AUTHORIZATION="Bearer photo-worker-token")
        non_ascii = self.post_json(url, body, HTTP_AUTHORIZATION="Bearer токен")
        whitespace = self.post_json(
            url, body, HTTP_AUTHORIZATION="Bearer dedicated-import-token extra"
        )
        accepted = self.post_json(url, body, HTTP_AUTHORIZATION="Bearer dedicated-import-token")

        for response in (missing, photo_worker, non_ascii, whitespace):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(
                response.json()["error"],
                {"code": "worker_unauthorized", "message": "Unauthorized."},
            )
        self.assertEqual(accepted.status_code, 200)

    def test_disabled_or_unconfigured_worker_capability_fails_closed(self) -> None:
        url = reverse("import_worker_claim")
        body = {"contract_version": 1, "lease_seconds": 120}
        with override_settings(PHOTO_IMPORT_ENABLED=False):
            disabled = self.post_json(url, body, HTTP_AUTHORIZATION="Bearer dedicated-import-token")
        with override_settings(PHOTO_IMPORT_WORKER_TOKEN=""):
            unconfigured = self.post_json(
                url, body, HTTP_AUTHORIZATION="Bearer dedicated-import-token"
            )

        self.assertEqual(disabled.status_code, 401)
        self.assertEqual(unconfigured.status_code, 401)
