"""Real standalone runner through private API and shared publication; no live storage."""

import hashlib
import io
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from feature_flags.registry import YANDEX_DISK_IMPORT
from import_worker.client import APIClient
from import_worker.contracts import Config
from import_worker.runner import Runner
from import_worker.source import DiskSource
from import_worker.transport import Response, Transport, TransportError
from ingestion.models import ImportAttempt, ImportBatch, ImportItem, UploadBatch, UploadItem
from ingestion.storage import ObjectIdentity, ObjectMissing, UploadGrant
from picflow.models import Event, Photo
from PIL import Image
from processing.models import PhotoProcessingState


class ByteStorage:
    def __init__(self):
        self.objects = {}

    def create_presigned_post(self, *, incoming_key, max_bytes):
        return UploadGrant(
            "https://storage.yandexcloud.net/fixture", {"key": incoming_key}, timezone.now()
        )

    def inspect(self, *, key):
        if key not in self.objects:
            raise ObjectMissing()
        digest = hashlib.md5(self.objects[key]).hexdigest()
        return ObjectIdentity(f'"{digest}"', digest, len(self.objects[key]), "image/jpeg")

    def read_range(self, *, key, etag_wire, start, end):
        assert self.inspect(key=key).etag_wire == etag_wire
        return self.objects[key][start : end + 1]

    def promote(self, *, incoming_key, final_key, etag_wire):
        assert self.inspect(key=incoming_key).etag_wire == etag_wire
        self.objects[final_key] = self.objects[incoming_key]
        return self.inspect(key=final_key)

    def delete(self, *, key):
        self.objects.pop(key, None)


class FixtureHTTP(Transport):
    def __init__(self, storage, jpeg, entries, lost_callbacks=()):
        self.api = Client()
        self.storage, self.jpeg, self.entries = storage, jpeg, entries
        self.downloads = []
        self.uploads = 0
        self.lost_callbacks = set(lost_callbacks)
        self.callback_bodies = {}
        self.accepted_upload_keys = []

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if kwargs["audience"] == "api":
            assert parsed.hostname == "web"
            response = self.api.post(
                parsed.path,
                data=kwargs["body"],
                content_type="application/json",
                HTTP_AUTHORIZATION=kwargs["headers"]["Authorization"],
            )
            operation = parsed.path.split("/", 6)[-1]
            self.callback_bodies.setdefault(parsed.path, []).append(kwargs["body"])
            if operation == "prepare-upload" and response.status_code == 200:
                grant = response.json()["upload"]["grant"]
                if grant is not None:
                    self.accepted_upload_keys.append(grant["fields"]["key"])
            if 200 <= response.status_code < 300 and operation in self.lost_callbacks:
                self.lost_callbacks.remove(operation)
                raise TransportError(retryable=True)
            return Response(response.status_code, {}, response.content)
        assert not kwargs.get("headers", {}).get("Authorization")
        assert not self.api.session.items()
        kwargs.get("heartbeat", lambda: None)()
        if kwargs["audience"] == "storage":
            data = b"".join(kwargs["body"])
            content_type = kwargs["headers"]["Content-Type"]
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + data
            )
            parts = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            key = parts["key"]
            assert isinstance(key, bytes)
            self.storage.objects[key.decode()] = parts["file"]
            self.uploads += 1
            return Response(204, {}, b"")
        if parsed.hostname == "cloud-api.yandex.net":
            if parsed.path.endswith("/download"):
                assert parse_qs(parsed.query)["public_key"] == ["canonical-fixture"]
                path = parse_qs(parsed.query)["path"][0]
                self.downloads.append(path)
                payload = dict(
                    href="https://downloader.disk.yandex.ru" + path, method="GET", templated=False
                )
            else:
                assert parse_qs(parsed.query)["public_key"] == ["https://disk.yandex.ru/d/fixture"]
                payload = dict(
                    type="dir",
                    public_key="canonical-fixture",
                    _embedded=dict(offset=0, total=len(self.entries), items=self.entries),
                )
            return Response(200, {}, json.dumps(payload).encode())
        data = b"bad jpeg" if parsed.path == "/invalid.jpg" else self.jpeg
        kwargs["sink"](data)
        return Response(200, {}, b"")


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    PHOTO_IMPORT_ENABLED=True,
    PHOTO_IMPORT_WORKER_TOKEN="fixture-token",
    PHOTO_PROCESSING_PREVIEW_ENABLED=False,
)
class ImportFlowTests(TestCase):
    def test_disconnected_browser_imports_real_jpeg_and_dedupes_without_local_upload_rows(self):
        self.run_import_flow()

    def test_accepted_callbacks_with_lost_responses_replay_without_source_attempts(self):
        self.run_import_flow({"manifest/pages", "manifest/finalize", "prepare-upload", "complete"})

    def run_import_flow(self, lost_callbacks=()):
        owner = get_user_model().objects.create_user(username="flow-owner")
        owner.user_permissions.add(
            Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")
        )
        event = Event.objects.create(
            name="Flow", slug="flow", city="Moscow", start_date="2026-09-07", end_date="2026-09-07"
        )
        FeatureFlag.objects.create(
            key=YANDEX_DISK_IMPORT.key,
            description=YANDEX_DISK_IMPORT.description,
            state=FeatureFlag.State.ON,
        )
        image = io.BytesIO()
        Image.new("RGB", (12, 8), "red").save(image, "JPEG")
        jpeg = image.getvalue()
        entries = [
            dict(type="file", path=f"/{name}", name=name, size=size, sha256=sha)
            for name, size, sha in [
                ("photo.jpg", len(jpeg), hashlib.sha256(jpeg).hexdigest()),
                ("duplicate.jpg", len(jpeg), ""),
                ("invalid.jpg", len(b"bad jpeg"), ""),
                ("changed.jpg", len(jpeg), "0" * 64),
            ]
        ]
        self.client.force_login(owner)
        created = self.client.post(
            reverse("import_collection"),
            data=json.dumps(
                dict(
                    contract_version=1,
                    event_id=event.pk,
                    folder_id=None,
                    source_url="https://disk.yandex.ru/d/fixture",
                    submission_key="fixture-submission",
                )
            ),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        self.client.logout()
        storage = ByteStorage()
        transport = FixtureHTTP(storage, jpeg, entries, lost_callbacks)
        with (
            TemporaryDirectory() as directory,
            patch("ingestion.import_worker_views.PrivateUploadStorage", return_value=storage),
        ):
            config = Config(
                "http://web/internal/photo-import/v1/", "fixture-token", Path(directory)
            )
            runner = Runner(
                config,
                APIClient(config, transport),
                DiskSource(transport),
                transport,
                sleep=lambda seconds: None,
            )
            try:
                for _ in range(5):
                    self.assertTrue(runner.run_once())
                self.assertFalse(runner.run_once())
                self.assertFalse(runner.path.exists())
            finally:
                runner.close()
        self.assertEqual(Photo.objects.filter(event=event).count(), 1)
        photo = Photo.objects.get(event=event)
        self.assertEqual(storage.objects[photo.original_key], jpeg)
        attempt = ImportAttempt.objects.get(item__photo=photo, item__status="imported")
        self.assertEqual((attempt.oriented_width, attempt.oriented_height), (12, 8))
        self.assertEqual(UploadBatch.objects.count(), 0)
        self.assertEqual(UploadItem.objects.count(), 0)
        self.assertEqual(
            set(
                PhotoProcessingState.objects.filter(photo=photo).values_list(
                    "processor_type", flat=True
                )
            ),
            {"capture_metadata", "generate_preview"},
        )
        self.assertEqual(transport.uploads, 1)
        self.assertEqual(len(transport.downloads), 4)
        self.assertEqual(ImportItem.objects.filter(status="imported").count(), 1)
        self.assertEqual(ImportItem.objects.filter(status="duplicate").count(), 1)
        self.assertEqual(
            set(ImportItem.objects.filter(status="error").values_list("error_code", flat=True)),
            {"invalid_jpeg", "source_changed"},
        )
        self.assertEqual(ImportBatch.objects.get().status, ImportBatch.Status.PARTIAL)

        self.assertEqual(ImportBatch.objects.get().manifest_attempts, 1)
        self.assertEqual(set(ImportItem.objects.values_list("download_attempts", flat=True)), {1})
        self.assertEqual(ImportItem.objects.get(status="imported").upload_attempts, 1)
        self.assertFalse(transport.lost_callbacks)
        self.assertEqual(set(transport.accepted_upload_keys), {attempt.incoming_key})
        self.assertEqual(ImportBatch.objects.get().manifest_pages.count(), 1)
        if lost_callbacks:
            self.assertEqual(len(transport.accepted_upload_keys), 2)
            replayed = [
                bodies
                for path, bodies in transport.callback_bodies.items()
                if path.split("/", 6)[-1] in lost_callbacks and len(bodies) == 2
            ]
            self.assertEqual(len(replayed), 4)
            self.assertTrue(all(bodies[0] == bodies[1] for bodies in replayed))

    def test_revisit_repeat_and_failed_item_retry_preserve_free_and_paid_policy(self):
        from feature_flags.registry import PAID_WATERMARKED_PREVIEWS
        from feature_flags.states import FEATURE_FLAG_ON
        from feature_flags.testing import override_feature_flags
        from ingestion.services.imports import create_import

        owner = get_user_model().objects.create_superuser("revisit", password="password")
        FeatureFlag.objects.create(key=YANDEX_DISK_IMPORT.key, state="on")
        image = io.BytesIO()
        Image.new("RGB", (12, 8), "blue").save(image, "JPEG")
        jpeg = image.getvalue()
        entries = [dict(type="file", path="/photo.jpg", name="photo.jpg", size=len(jpeg))]
        for paid in (False, True):
            with (
                self.subTest(paid=paid),
                override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True),
                override_feature_flags(
                    {
                        PAID_WATERMARKED_PREVIEWS: FEATURE_FLAG_ON,
                        YANDEX_DISK_IMPORT: FEATURE_FLAG_ON,
                    }
                ),
            ):
                event = Event.objects.create(
                    name=f"Revisit-{paid}",
                    slug=f"revisit-{paid}",
                    city="Moscow",
                    start_date="2026-09-07",
                    end_date="2026-09-07",
                    price_per_photo_kopecks=30000 if paid else None,
                    access_type="paid" if paid else "free",
                    timezone_name="Europe/Moscow",
                )
                storage = ByteStorage()
                transport = FixtureHTTP(storage, jpeg, entries)
                source = DiskSource(transport)
                self.client.force_login(owner)
                submitted = self.client.post(
                    reverse("import_collection"),
                    data=json.dumps(
                        dict(
                            contract_version=1,
                            event_id=event.pk,
                            folder_id=None,
                            source_url="https://disk.yandex.ru/d/fixture",
                            submission_key=f"first-{paid}",
                        )
                    ),
                    content_type="application/json",
                )
                self.assertEqual(submitted.status_code, 201)
                batch = ImportBatch.objects.get(event=event)
                self.client.logout()
                with (
                    TemporaryDirectory() as directory,
                    patch(
                        "ingestion.import_worker_views.PrivateUploadStorage", return_value=storage
                    ),
                ):
                    config = Config(
                        "http://web/internal/photo-import/v1/", "fixture-token", Path(directory)
                    )
                    runner = Runner(
                        config,
                        APIClient(config, transport),
                        source,
                        transport,
                        sleep=lambda seconds: None,
                    )
                    try:
                        self.assertTrue(runner.run_once())
                        with patch.object(
                            source, "download_url", side_effect=TransportError(retryable=True)
                        ):
                            for _ in range(4):
                                self.assertTrue(runner.run_once())
                        self.assertEqual(ImportItem.objects.get(batch=batch).status, "error")
                        self.client.force_login(owner)
                        detail = self.client.get(
                            reverse("import_detail", kwargs={"batch": batch.pk})
                        )
                        self.assertEqual(detail.status_code, 200)
                        retried = self.client.post(
                            reverse("import_retry", kwargs={"batch": batch.pk}),
                            data=json.dumps(dict(contract_version=1)),
                            content_type="application/json",
                        )
                        self.assertEqual(retried.status_code, 200)
                        self.client.logout()
                        self.assertTrue(runner.run_once())
                        photo = Photo.objects.get(event=event)
                        expected = "preview_first_watermarked_v1" if paid else "preview_first_v1"
                        self.assertEqual(photo.processing_generation, expected)
                        processors = set(
                            photo.processing_states.values_list("processor_type", flat=True)
                        )
                        self.assertIn("capture_metadata", processors)
                        self.assertIn(
                            "generate_preview",
                            processors,
                        )
                        jobs = photo.processing_jobs.count()
                        repeated = create_import(
                            actor=owner,
                            event=event,
                            folder=None,
                            submitted_source_key="fixture",
                            submission_key=f"repeated-{paid}",
                        )
                        self.assertTrue(runner.run_once())
                        self.assertTrue(runner.run_once())
                        self.assertEqual(
                            ImportItem.objects.get(batch_id=repeated.id).status, "duplicate"
                        )
                        self.assertEqual(Photo.objects.filter(event=event).count(), 1)
                        self.assertEqual(photo.processing_jobs.count(), jobs)
                    finally:
                        runner.close()
