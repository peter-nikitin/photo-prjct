from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from feature_flags.registry import YANDEX_DISK_IMPORT
from ingestion.models import ImportAttempt, ImportBatch, ImportItem, ImportScope
from ingestion.services.import_publication import ImportCompletion
from ingestion.services.imports import ImportConflict
from ingestion.storage import UploadGrant
from picflow.models import Event, EventFolder, Photo
from processing.models import PhotoProcessingState


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    PHOTO_IMPORT_ENABLED=True,
    PHOTO_IMPORT_WORKER_TOKEN="import-worker-secret",
    PHOTO_IMPORT_MAX_JSON_BYTES=1024 * 1024,
)
class ImportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        users = get_user_model().objects
        cls.owner = users.create_user(username="import-owner", password="password")
        permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        cls.owner.user_permissions.add(permission)
        cls.event = Event.objects.create(
            name="Import event",
            slug="import-event",
            start_date="2026-09-01",
            end_date="2026-09-01",
            city="Moscow",
        )
        cls.folder = EventFolder.objects.create(event=cls.event, name="Finish")

    def setUp(self) -> None:
        FeatureFlag.objects.create(
            key=YANDEX_DISK_IMPORT.key,
            description=YANDEX_DISK_IMPORT.description,
            state=FeatureFlag.State.ON,
        )
        self.client.force_login(self.owner)

    def json_post(self, url: str, data: Mapping[str, object], **extra: object):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
            **extra,
        )

    def worker_post(self, url: str, data: Mapping[str, object], **extra: object):
        return self.json_post(
            url,
            data,
            HTTP_AUTHORIZATION="Bearer import-worker-secret",
            **extra,
        )

    def create_import(self, *, submission_key: str = "submission-1"):
        return self.json_post(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": self.folder.pk,
                "source_url": "https://disk.yandex.ru/d/public-key_1",
                "submission_key": submission_key,
            },
        )

    def test_browser_create_replays_submission_and_never_returns_source_identity(self) -> None:
        first = self.create_import()
        replay = self.create_import()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        payload = first.content.decode()
        self.assertNotIn("public-key_1", payload)
        self.assertNotIn("disk.yandex", payload)
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_browser_duplicate_submission_key_rejects_a_changed_payload(self) -> None:
        self.create_import()

        conflict = self.json_post(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": self.folder.pk,
                "source_url": "https://disk.yandex.ru/d/different-key",
                "submission_key": "submission-1",
            },
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "submission_conflict")
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_browser_list_detail_and_items_are_bounded_and_redacted(self) -> None:
        created = self.create_import().json()["batch"]
        batch = ImportBatch.objects.get(pk=created["id"])
        batch.submitted_source_key = "https://secret.example/source?token=secret"
        batch.save(update_fields=["submitted_source_key"])
        ImportItem.objects.create(
            batch=batch,
            source_path="/secret/source/photo.jpg",
            filename="photo.jpg",
            byte_size=123,
            status=ImportItem.Status.ERROR,
            error_code="download_unavailable",
        )

        responses = (
            self.client.get(reverse("import_collection")),
            self.client.get(reverse("import_detail", args=[batch.pk])),
            self.client.get(reverse("import_items", args=[batch.pk]), {"page_size": 100}),
        )

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(response.content), 1024 * 1024)
            body = response.content.decode()
            self.assertNotIn("secret.example", body)
            self.assertNotIn("/secret/source", body)
            self.assertNotIn("token=", body)
        self.assertEqual(responses[2].json()["items"][0]["filename"], "photo.jpg")

    def test_browser_batch_processing_activity_comes_from_existing_photo_state(self) -> None:
        batch = ImportBatch.objects.get(pk=self.create_import().json()["batch"]["id"])
        batch.status = ImportBatch.Status.COMPLETED
        batch.jpeg_count = 1
        batch.save(update_fields=["status", "jpeg_count"])
        photo = Photo.objects.create(
            id="import-photo",
            event=self.event,
            src="",
            uploaded_by=self.owner,
            original_key="originals/import-photo",
            original_filename="photo.jpg",
            original_size=123,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        ImportItem.objects.create(
            batch=batch,
            source_path="/photo.jpg",
            filename="photo.jpg",
            byte_size=123,
            status=ImportItem.Status.IMPORTED,
            photo=photo,
        )
        state = PhotoProcessingState.objects.create(
            photo=photo,
            processor_type="generate_preview",
            status=PhotoProcessingState.Status.QUEUED,
        )

        listed = self.client.get(reverse("import_collection"))
        detailed = self.client.get(reverse("import_detail", args=[batch.pk]))

        self.assertIs(listed.json()["imports"][0]["processing_active"], True)
        self.assertIs(detailed.json()["batch"]["processing_active"], True)

        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.save(update_fields=["status"])
        refreshed = self.client.get(reverse("import_detail", args=[batch.pk]))

        self.assertIs(refreshed.json()["batch"]["processing_active"], False)

    def test_browser_endpoints_reject_unknown_contract_and_invalid_source_without_rows(
        self,
    ) -> None:
        unknown = self.json_post(
            reverse("import_collection"),
            {
                "contract_version": 2,
                "event_id": self.event.pk,
                "folder_id": None,
                "source_url": "https://disk.yandex.ru/d/key",
                "submission_key": "unknown-version",
            },
        )
        invalid = self.json_post(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": None,
                "source_url": "http://127.0.0.1/private",
                "submission_key": "invalid-source",
            },
        )

        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()["error"]["code"], "unsupported_contract")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")
        self.assertFalse(ImportBatch.objects.exists())

    def test_browser_rejects_a_malformed_port_without_echoing_the_source(self) -> None:
        response = self.json_post(
            reverse("import_collection"),
            {
                "contract_version": 1,
                "event_id": self.event.pk,
                "folder_id": None,
                "source_url": "https://disk.yandex.ru:not-a-port/d/secret-key",
                "submission_key": "bad-port",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertNotIn("secret-key", response.content.decode())

    def test_worker_manifest_callback_replay_and_finalize_replay_are_idempotent(self) -> None:
        batch = ImportBatch.objects.get(pk=self.create_import().json()["batch"]["id"])
        claim = self.worker_post(
            reverse("import_worker_claim"),
            {"contract_version": 1, "lease_seconds": 120},
        ).json()["work"]
        page_url = reverse("import_worker_manifest_page", args=[claim["attempt_id"]])
        page = {
            "contract_version": 1,
            "batch_id": str(batch.pk),
            "page_number": 0,
            "page_fingerprint": "page-0",
            "entries": [
                {
                    "path": "/photo.jpg",
                    "name": "photo.jpg",
                    "kind": "jpeg",
                    "size": 123,
                    "sha256": None,
                    "md5": None,
                    "version": "v1",
                },
                {
                    "path": "/empty.txt",
                    "name": "empty.txt",
                    "kind": "unsupported",
                    "size": 0,
                    "sha256": None,
                    "md5": None,
                    "version": "v1",
                },
            ],
        }

        first_page = self.worker_post(page_url, page)
        replay_page = self.worker_post(page_url, page)
        finalize_url = reverse("import_worker_manifest_finalize", args=[claim["attempt_id"]])
        finalize = {
            "contract_version": 1,
            "batch_id": str(batch.pk),
            "canonical_source_key": "canonical-key",
        }
        first_finalize = self.worker_post(finalize_url, finalize)
        replay_finalize = self.worker_post(finalize_url, finalize)

        self.assertEqual(first_page.status_code, 200)
        self.assertFalse(first_page.json()["page"]["replayed"])
        self.assertTrue(replay_page.json()["page"]["replayed"])
        self.assertEqual(first_finalize.status_code, 200)
        self.assertEqual(replay_finalize.status_code, 200)
        self.assertEqual(first_finalize.json(), replay_finalize.json())
        self.assertEqual(batch.items.count(), 1)

    def test_worker_duplicate_manifest_paths_return_a_sanitized_conflict(self) -> None:
        batch = ImportBatch.objects.get(pk=self.create_import().json()["batch"]["id"])
        claim = self.worker_post(
            reverse("import_worker_claim"),
            {"contract_version": 1, "lease_seconds": 120},
        ).json()["work"]
        entry = {
            "path": "/duplicate.jpg",
            "name": "duplicate.jpg",
            "kind": "jpeg",
            "size": 123,
            "sha256": None,
            "md5": None,
            "version": "v1",
        }
        response = self.worker_post(
            reverse("import_worker_manifest_page", args=[claim["attempt_id"]]),
            {
                "contract_version": 1,
                "batch_id": str(batch.pk),
                "page_number": 0,
                "page_fingerprint": "duplicates",
                "entries": [entry, entry],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "manifest_changed")
        self.assertNotIn("duplicate.jpg", response.content.decode())
        self.assertFalse(batch.items.exists())

    def test_worker_claim_file_and_prepare_upload_freeze_private_grant_contract(self) -> None:
        batch = ImportBatch.objects.get(pk=self.create_import().json()["batch"]["id"])
        batch.scope = ImportScope.objects.create(
            owner=self.owner,
            event=self.event,
            folder=self.folder,
            canonical_source_key="canonical-key",
        )
        batch.status = ImportBatch.Status.TRANSFERRING
        batch.manifest_complete = True
        batch.save(update_fields=["scope", "status", "manifest_complete"])
        item = ImportItem.objects.create(
            batch=batch,
            source_path="/photo.jpg",
            filename="photo.jpg",
            byte_size=123,
            source_version="v1",
        )
        claimed = self.worker_post(
            reverse("import_worker_claim"),
            {"contract_version": 1, "lease_seconds": 120},
        )
        work = claimed.json()["work"]
        grant = UploadGrant(
            url="https://storage.example/upload",
            fields={"key": "incoming/private", "policy": "short"},
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        with patch(
            "ingestion.import_worker_views.PrivateUploadStorage.create_presigned_post",
            return_value=grant,
        ):
            prepared = self.worker_post(
                reverse("import_worker_prepare_upload", args=[work["attempt_id"]]),
                {
                    "contract_version": 1,
                    "item_id": str(item.pk),
                    "content_sha256": "a" * 64,
                    "byte_size": 123,
                    "oriented_geometry": {"width": 10, "height": 20},
                },
            )

        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(work["kind"], "file")
        self.assertEqual(work["source"]["path"], "/photo.jpg")
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.json()["upload"]["status"], "upload")
        self.assertEqual(prepared.json()["upload"]["grant"]["fields"]["policy"], "short")

        oversized_grant = UploadGrant(
            url="https://storage.example/upload",
            fields={"policy": "x" * 1024},
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        with (
            override_settings(PHOTO_IMPORT_MAX_JSON_BYTES=512),
            patch(
                "ingestion.import_worker_views.PrivateUploadStorage.create_presigned_post",
                return_value=oversized_grant,
            ),
        ):
            oversized_response = self.worker_post(
                reverse("import_worker_prepare_upload", args=[work["attempt_id"]]),
                {
                    "contract_version": 1,
                    "item_id": str(item.pk),
                    "content_sha256": "a" * 64,
                    "byte_size": 123,
                    "oriented_geometry": {"width": 10, "height": 20},
                },
            )
        self.assertEqual(oversized_response.status_code, 500)
        self.assertLessEqual(len(oversized_response.content), 512)
        self.assertEqual(oversized_response.json()["error"]["code"], "response_too_large")

        with patch(
            "ingestion.import_worker_views.complete_import_item",
            return_value=ImportCompletion("imported", item.pk, "photo-id"),
        ):
            completed = self.worker_post(
                reverse("import_worker_complete", args=[work["attempt_id"]]),
                {"contract_version": 1, "item_id": str(item.pk)},
            )
        self.assertEqual(
            completed.json()["completion"],
            {"item_id": str(item.pk), "status": "imported", "photo_id": "photo-id"},
        )

    def test_worker_failure_callback_and_unknown_version_have_stable_contracts(self) -> None:
        batch = ImportBatch.objects.get(pk=self.create_import().json()["batch"]["id"])
        claim = self.worker_post(
            reverse("import_worker_claim"),
            {"contract_version": 1, "lease_seconds": 120},
        ).json()["work"]
        unknown = self.worker_post(
            reverse("import_worker_renew", args=[claim["attempt_id"]]),
            {"contract_version": 2, "lease_seconds": 120},
        )
        failed = self.worker_post(
            reverse("import_worker_fail", args=[claim["attempt_id"]]),
            {
                "contract_version": 1,
                "operation": "manifest",
                "code": "source_unavailable",
                "retryable": True,
            },
        )

        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()["contract_version"], 1)
        self.assertEqual(unknown.json()["error"]["code"], "unsupported_contract")
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["failure"], {"batch_id": str(batch.pk), "status": "queued"})

    def test_worker_rejects_more_than_100_manifest_entries_before_state_change(self) -> None:
        batch = ImportBatch.objects.get(pk=self.create_import().json()["batch"]["id"])
        claim = self.worker_post(
            reverse("import_worker_claim"),
            {"contract_version": 1, "lease_seconds": 120},
        ).json()["work"]
        entries = [
            {
                "path": f"/{index}.jpg",
                "name": f"{index}.jpg",
                "kind": "jpeg",
                "size": 1,
                "sha256": None,
                "md5": None,
                "version": "",
            }
            for index in range(101)
        ]
        response = self.worker_post(
            reverse("import_worker_manifest_page", args=[claim["attempt_id"]]),
            {
                "contract_version": 1,
                "batch_id": str(batch.pk),
                "page_number": 0,
                "page_fingerprint": "oversized-page",
                "entries": entries,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertFalse(batch.manifest_pages.exists())
        self.assertFalse(batch.items.exists())

    def test_worker_rejects_oversized_and_invalid_json_with_sanitized_errors(self) -> None:
        url = reverse("import_worker_claim")
        oversized = self.client.post(
            url,
            data=b"{" + b" " * (1024 * 1024),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer import-worker-secret",
        )
        invalid_utf8 = self.client.post(
            url,
            data=b"\xffhttps://secret.example/?token=leak",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer import-worker-secret",
        )

        for response in (oversized, invalid_utf8):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["contract_version"], 1)
            self.assertEqual(response.json()["error"]["code"], "invalid_request")
            self.assertNotIn("secret.example", response.content.decode())
            self.assertNotIn("token=", response.content.decode())

    def test_worker_returns_sanitized_stale_lease_error(self) -> None:
        self.create_import()
        claim = self.worker_post(
            reverse("import_worker_claim"),
            {"contract_version": 1, "lease_seconds": 1},
        ).json()["work"]
        ImportAttempt.objects.filter(pk=claim["attempt_id"]).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.worker_post(
            reverse("import_worker_renew", args=[claim["attempt_id"]]),
            {"contract_version": 1, "lease_seconds": 120},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["contract_version"], 1)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "lease_not_current",
                "message": "The import lease is no longer current.",
            },
        )

    def test_worker_rejects_wrong_scalar_types_before_service_mutation(self) -> None:
        attempt_id = "00000000-0000-0000-0000-000000000001"
        invalid_failures = (
            {"operation": [], "code": "source_unavailable", "retryable": True},
            {"operation": "manifest", "code": [], "retryable": True},
        )
        with patch("ingestion.import_worker_views.record_import_failure") as record_failure:
            responses = [
                self.worker_post(
                    reverse("import_worker_fail", args=[attempt_id]),
                    {"contract_version": 1, **fields},
                )
                for fields in invalid_failures
            ]
        malformed_entries = (
            {"path": [], "kind": "jpeg"},
            {"path": "/photo.jpg", "kind": []},
        )
        with patch("ingestion.import_worker_views.record_manifest_page") as record_page:
            for fields in malformed_entries:
                responses.append(
                    self.worker_post(
                        reverse("import_worker_manifest_page", args=[attempt_id]),
                        {
                            "contract_version": 1,
                            "batch_id": "00000000-0000-0000-0000-000000000002",
                            "page_number": 0,
                            "page_fingerprint": "page-0",
                            "entries": [
                                {
                                    "path": fields["path"],
                                    "name": "photo.jpg",
                                    "kind": fields["kind"],
                                    "size": 1,
                                    "sha256": None,
                                    "md5": None,
                                    "version": "v1",
                                }
                            ],
                        },
                    )
                )

        self.assertFalse(record_failure.called)
        self.assertFalse(record_page.called)
        for response in responses:
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_worker_rejects_huge_json_numbers_and_database_overflow_before_mutation(self) -> None:
        attempt_id = "00000000-0000-0000-0000-000000000001"
        huge_json = self.client.post(
            reverse("import_worker_claim"),
            data=b'{"contract_version":1,"lease_seconds":' + b"9" * 5000 + b"}",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer import-worker-secret",
        )
        with patch("ingestion.import_worker_views.record_manifest_page") as record_page:
            oversized_manifest = self.worker_post(
                reverse("import_worker_manifest_page", args=[attempt_id]),
                {
                    "contract_version": 1,
                    "batch_id": "00000000-0000-0000-0000-000000000002",
                    "page_number": 2_147_483_648,
                    "page_fingerprint": "page-overflow",
                    "entries": [],
                },
            )
            oversized_size = self.worker_post(
                reverse("import_worker_manifest_page", args=[attempt_id]),
                {
                    "contract_version": 1,
                    "batch_id": "00000000-0000-0000-0000-000000000002",
                    "page_number": 0,
                    "page_fingerprint": "size-overflow",
                    "entries": [
                        {
                            "path": "/photo.jpg",
                            "name": "photo.jpg",
                            "kind": "jpeg",
                            "size": 9_223_372_036_854_775_808,
                            "sha256": None,
                            "md5": None,
                            "version": "v1",
                        }
                    ],
                },
            )
        with patch("ingestion.import_worker_views.prepare_import_upload") as prepare_upload:
            oversized_geometry = self.worker_post(
                reverse("import_worker_prepare_upload", args=[attempt_id]),
                {
                    "contract_version": 1,
                    "item_id": "00000000-0000-0000-0000-000000000003",
                    "content_sha256": "a" * 64,
                    "byte_size": 1,
                    "oriented_geometry": {"width": 2_147_483_648, "height": 1},
                },
            )

        self.assertFalse(record_page.called)
        self.assertFalse(prepare_upload.called)
        for response in (huge_json, oversized_manifest, oversized_size, oversized_geometry):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_browser_rejects_huge_numeric_inputs_before_service_mutation(self) -> None:
        huge_json = self.client.post(
            reverse("import_collection"),
            data=(
                b'{"contract_version":1,"event_id":'
                + b"9" * 5000
                + b',"folder_id":null,"source_url":"https://disk.yandex.ru/d/key",'
                b'"submission_key":"huge-json"}'
            ),
            content_type="application/json",
        )
        huge_page = self.client.get(reverse("import_collection"), {"page": "9" * 5000})
        with patch("ingestion.import_views.create_import") as create:
            oversized_id = self.json_post(
                reverse("import_collection"),
                {
                    "contract_version": 1,
                    "event_id": 9_223_372_036_854_775_808,
                    "folder_id": None,
                    "source_url": "https://disk.yandex.ru/d/key",
                    "submission_key": "oversized-id",
                },
            )

        self.assertFalse(create.called)
        self.assertFalse(ImportBatch.objects.exists())
        for response in (huge_json, huge_page, oversized_id):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_worker_publication_errors_keep_retryable_and_terminal_classification(self) -> None:
        attempt_id = "00000000-0000-0000-0000-000000000001"
        item_id = "00000000-0000-0000-0000-000000000002"
        responses = {}
        for code in ("storage_unavailable", "invalid_jpeg", "size_mismatch"):
            with patch(
                "ingestion.import_worker_views.complete_import_item",
                side_effect=ImportConflict(code, "private storage detail"),
            ):
                responses[code] = self.worker_post(
                    reverse("import_worker_complete", args=[attempt_id]),
                    {"contract_version": 1, "item_id": item_id},
                )

        temporary = responses["storage_unavailable"]
        self.assertEqual(temporary.status_code, 503)
        self.assertEqual(temporary.json()["error"]["code"], "storage_unavailable")
        self.assertTrue(temporary.json()["error"]["retryable"])
        for code in ("invalid_jpeg", "size_mismatch"):
            terminal = responses[code]
            self.assertEqual(terminal.status_code, 409)
            self.assertEqual(terminal.json()["error"]["code"], code)
            self.assertFalse(terminal.json()["error"]["retryable"])
            self.assertNotIn("private storage detail", terminal.content.decode())


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    PHOTO_IMPORT_ENABLED=True,
    PHOTO_IMPORT_WORKER_TOKEN="import-worker-secret",
    PHOTO_IMPORT_MAX_JSON_BYTES=1024 * 1024,
)
class ImportBrowserCsrfTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.owner = get_user_model().objects.create_user(username="csrf-owner", password="password")
        cls.owner.user_permissions.add(
            Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")
        )
        cls.event = Event.objects.create(
            name="CSRF event",
            slug="csrf-event",
            start_date="2026-09-01",
            end_date="2026-09-01",
            city="Moscow",
        )

    def test_browser_mutation_requires_csrf_while_worker_token_endpoint_does_not(self) -> None:
        FeatureFlag.objects.create(
            key=YANDEX_DISK_IMPORT.key,
            description=YANDEX_DISK_IMPORT.description,
            state=FeatureFlag.State.ON,
        )
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.owner)
        browser = client.post(
            reverse("import_collection"), data="{}", content_type="application/json"
        )
        worker = client.post(
            reverse("import_worker_claim"),
            data=json.dumps({"contract_version": 1, "lease_seconds": 120}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer import-worker-secret",
        )

        self.assertEqual(browser.status_code, 403)
        self.assertEqual(worker.status_code, 200)
