"""Real-JPEG proof across the Django worker API and standalone worker iteration."""

from __future__ import annotations

import hashlib
import io
import sys
import tempfile
from datetime import date, timedelta
from email.message import Message
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from ingestion.models import UploadItem
from ingestion.services.batches import (
    AuthorizationReason,
    BatchInput,
    ItemInput,
    authorize_item,
    create_batch,
    register_items,
)
from ingestion.services.confirmation import confirm_upload_item
from ingestion.storage import ObjectIdentity, ObjectMissing, UploadGrant
from picflow.gallery import gallery_photo_queryset
from picflow.models import Event, Photo
from PIL import Image
from processing.models import (
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
)
from processing.services.enrollment import request_capture_metadata, request_face_embedding_enqueue
from processing.storage import DownloadGrant, PreviewObject, PreviewUploadGrant

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "worker"))

from photo_worker.client import DownloadError, HttpClient  # noqa: E402
from photo_worker.face_embedding import FaceEmbeddingError  # noqa: E402
from photo_worker.runner import Worker, WorkerConfig  # noqa: E402

_OBJECT_URL = "https://object.test/original.jpg?one-time-grant"
_PREVIEW_UPLOAD_URL = "https://object.test/preview.jpg?one-time-grant"


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self._body.close()

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class _ConfirmationStorage:
    """Small in-memory confirmation boundary; Django confirmation remains real."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}

    def add(self, key: str, content: bytes) -> None:
        self.objects[key] = (content, '"original-etag"', "image/jpeg")

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        return UploadGrant("https://upload.test", {}, timezone.now() + timedelta(minutes=10))

    def inspect(self, *, key: str) -> ObjectIdentity:
        try:
            content, etag, content_type = self.objects[key]
        except KeyError:
            raise ObjectMissing() from None
        return ObjectIdentity(etag, etag.strip('"'), len(content), content_type)

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes:
        content, actual_etag, _ = self.objects[key]
        if etag_wire != actual_etag:
            from ingestion.storage import ObjectChanged

            raise ObjectChanged()
        return content[start : end + 1]

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity:
        content, actual_etag, content_type = self.objects[incoming_key]
        if etag_wire != actual_etag:
            from ingestion.storage import ObjectChanged

            raise ObjectChanged()
        self.objects[final_key] = (content, actual_etag, content_type)
        return self.inspect(key=final_key)

    def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


class _PreviewStorage:
    """Exact-object boundary for the e2e transport, with optional verified-object corruption."""

    def __init__(self, *, reject_staging_metadata: bool = False) -> None:
        self._staging: dict[str, bytes] = {}
        self._final: dict[str, bytes] = {}
        self._reject_staging_metadata = reject_staging_metadata

    def create_upload_grant(
        self, *, staging_key: str, max_ttl_seconds: int | None = None
    ) -> PreviewUploadGrant:
        assert max_ttl_seconds is not None and max_ttl_seconds > 0
        return PreviewUploadGrant(_PREVIEW_UPLOAD_URL, timezone.now() + timedelta(seconds=30))

    def upload(self, *, staging_key: str, content: bytes) -> None:
        self._staging[staging_key] = content

    def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
        try:
            content = self._final[key] if key.startswith("derivatives/") else self._staging[key]
        except KeyError:
            raise ObjectMissing() from None
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
        byte_size = len(content)
        if self._reject_staging_metadata and key.startswith("processing-staging/"):
            byte_size += 1
        return PreviewObject(
            etag_wire='"preview-etag"',
            etag_value="preview-etag",
            byte_size=byte_size,
            content_type="image/jpeg",
            sha256=hashlib.sha256(content).hexdigest(),
            width=width,
            height=height,
        )

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject:
        content = self._staging[staging_key]
        self._final.setdefault(final_key, content)
        return self.verify(key=final_key, max_bytes=10_485_760)


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_FACE_ENABLED=True,
    PHOTO_PROCESSING_PREVIEW_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="e2e-worker-token",
)
class PipelineEndToEndTests(TestCase):
    """Only the exact-object transport is fake; queue and API calls are real Django code."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="pipeline-owner")
        self.event = Event.objects.create(
            name="Pipeline event",
            slug="pipeline-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Etc/UTC",
        )
        self.jpeg = self._jpeg_with_capture_time()
        self._make_completion_stale = False
        self._preview_storage: _PreviewStorage | None = None
        self._preview_staging_key: str | None = None

    def test_confirmed_preview_first_jpeg_is_published_for_gallery_before_face_v2_is_queued(
        self,
    ) -> None:
        """Catch any bypass of confirmation → preview → publication → preview-backed ML."""
        photo = self._confirmed_preview_photo()
        preview_storage = _PreviewStorage()

        preview = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        self.assertEqual(preview.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(
            (preview.current_job.contract_version, preview.current_job.processor_version), (2, 1)
        )
        self.assertEqual(face.status, PhotoProcessingState.Status.NOT_REQUESTED)
        self.assertIsNone(face.current_job_id)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())
        self.assertFalse(gallery_photo_queryset(event=self.event).filter(pk=photo.pk).exists())

        self._run_preview_worker(preview_storage)

        preview = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        self.assertEqual(
            preview.status,
            PhotoProcessingState.Status.SUCCEEDED,
            msg=preview.current_attempt.error_code if preview.current_attempt_id else "no attempt",
        )
        derivative = PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")

        self.assertEqual(derivative.accepted_attempt_id, preview.accepted_attempt_id)
        self.assertEqual((derivative.width, derivative.height), (1600, 800))
        self.assertEqual(
            list(gallery_photo_queryset(event=self.event).values_list("id", flat=True)), [photo.id]
        )
        self.assertEqual(face.status, PhotoProcessingState.Status.QUEUED)
        self.assertEqual(
            (face.current_job.contract_version, face.current_job.processor_version), (2, 2)
        )
        self.assertEqual(face.current_job.input_fingerprint["media_kind"], "preview-small-v1")
        self.assertEqual(face.current_job.input_fingerprint["object_key"], derivative.final_key)
        self.assertEqual(face.current_job.input_fingerprint["pixel_width"], derivative.width)

    def test_stale_preview_completion_keeps_preview_required_photo_hidden_and_face_unrequested(
        self,
    ) -> None:
        """Catch a late worker success that would expose media or enqueue ML after lease loss."""
        photo = self._confirmed_preview_photo()
        preview_storage = _PreviewStorage()
        self._make_completion_stale = True

        self._run_preview_worker(preview_storage)

        preview = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())
        self.assertNotEqual(preview.status, PhotoProcessingState.Status.SUCCEEDED)
        self.assertEqual(face.status, PhotoProcessingState.Status.NOT_REQUESTED)
        self.assertFalse(gallery_photo_queryset(event=self.event).filter(pk=photo.pk).exists())

    def test_rejected_preview_staging_metadata_keeps_photo_hidden_and_face_unrequested(
        self,
    ) -> None:
        """Catch acceptance of an uploaded object whose independently verified facts disagree."""
        photo = self._confirmed_preview_photo()

        self._run_preview_worker(_PreviewStorage(reject_staging_metadata=True))

        preview = PhotoProcessingState.objects.get(photo=photo, processor_type="generate_preview")
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        self.assertEqual(preview.status, PhotoProcessingState.Status.FAILED)
        self.assertFalse(PhotoDerivative.objects.filter(photo=photo).exists())
        self.assertEqual(face.status, PhotoProcessingState.Status.NOT_REQUESTED)
        self.assertFalse(gallery_photo_queryset(event=self.event).filter(pk=photo.pk).exists())

    def test_one_worker_iteration_claims_downloads_extracts_and_closes_immutable_event_report(
        self,
    ) -> None:
        """Catch a broken pipeline boundary that isolated unit tests can leave green."""
        photo = Photo.objects.create(
            id="0123456789abcdef0123456789abcdef",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_filename="pipeline.jpg",
            original_size=len(self.jpeg),
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        request_capture_metadata(photo)

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = Worker(
                HttpClient(
                    "http://django.test/internal/photo-processing/v1",
                    "e2e-worker-token",
                    opener=self._open,
                ),
                WorkerConfig(
                    worker_build="pipeline-e2e",
                    lease_seconds=120,
                    temp_dir=Path(temp_dir),
                    log_secrets=("e2e-worker-token",),
                ),
            )
            with patch(
                "processing.views.ExactObjectDownloadStorage.create_download_grant",
                return_value=DownloadGrant(
                    url=_OBJECT_URL, expires_at=timezone.now() + timedelta(seconds=30)
                ),
            ):
                assert worker.run_once() is None

        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        attempt = ProcessingAttempt.objects.get(job=state.current_job)
        run = EventProcessingRun.objects.get(pk=state.current_run_id)

        self.assertEqual(state.status, PhotoProcessingState.Status.SUCCEEDED)
        self.assertEqual(attempt.status, ProcessingAttempt.Status.SUCCEEDED)
        self.assertTrue(attempt.accepted)
        self.assertEqual(attempt.result["capture_time"], "2026-07-29T10:11:12Z")
        self.assertEqual(attempt.result["source_field"], "DateTimeOriginal")
        self.assertEqual(run.status, EventProcessingRun.Status.CLOSED)
        self.assertEqual(run.report["event_id"], str(self.event.id))
        self.assertEqual(
            run.report["counts"], {"denominator": 1, "succeeded": 1, "failed": 0, "cancelled": 0}
        )
        self.assertEqual(run.report["photos"][0]["photo_id"], photo.id)
        self.assertEqual(run.report["photos"][0]["capture_time_present"], True)
        with self.assertRaises(IntegrityError):
            EventProcessingRun.objects.filter(pk=run.pk).update(report={"tampered": True})

    def test_noncanonical_exif_is_completed_as_malformed_metadata_not_left_processing(self) -> None:
        """A worker result Django rejects must not stop the daemon with a live lease."""
        self.jpeg = self._jpeg_with_capture_time("2026:7:9 1:2:3")
        photo = Photo.objects.create(
            id="fedcba9876543210fedcba9876543210",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/fedcba9876543210fedcba9876543210",
            original_filename="noncanonical.jpg",
            original_size=len(self.jpeg),
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        request_capture_metadata(photo)

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = Worker(
                HttpClient(
                    "http://django.test/internal/photo-processing/v1",
                    "e2e-worker-token",
                    opener=self._open,
                ),
                WorkerConfig(
                    worker_build="pipeline-e2e",
                    lease_seconds=120,
                    temp_dir=Path(temp_dir),
                    log_secrets=("e2e-worker-token",),
                ),
            )
            with patch(
                "processing.views.ExactObjectDownloadStorage.create_download_grant",
                return_value=DownloadGrant(
                    url=_OBJECT_URL, expires_at=timezone.now() + timedelta(seconds=30)
                ),
            ):
                self.assertIsNone(worker.run_once())

        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        attempt = ProcessingAttempt.objects.get(job=state.current_job)
        self.assertEqual(state.status, PhotoProcessingState.Status.SUCCEEDED)
        self.assertEqual(attempt.status, ProcessingAttempt.Status.SUCCEEDED)
        self.assertTrue(attempt.accepted)
        self.assertEqual(
            attempt.result,
            {
                "capture_time": None,
                "source_field": None,
                "timezone_state": "not_applicable",
                "source_value": None,
                "source_offset": None,
                "event_timezone": "Etc/UTC",
                "warnings": ["capture_time_malformed", "capture_time_missing"],
            },
        )

    def test_worker_face_failure_envelopes_are_accepted_by_django(self) -> None:
        """Catch drift between worker-produced face failures and Django's exact
        envelope contract."""
        cases = (
            ("model_inference_error", False, "processor"),
            ("download_authorization_expired", True, "download"),
            ("fingerprint_mismatch", True, "download"),
            ("unsupported_input", False, "processor"),
        )

        for index, (error_code, retryable, failure_phase) in enumerate(cases, start=1):
            with self.subTest(error_code=error_code):
                photo_id = f"{index:032x}"
                photo = Photo.objects.create(
                    id=photo_id,
                    event=self.event,
                    src="",
                    uploaded_by=self.user,
                    original_key=f"originals/{photo_id}",
                    original_filename="face-failure.jpg",
                    original_size=len(self.jpeg),
                    original_content_type="image/jpeg",
                    uploaded_at=timezone.now(),
                )
                request_face_embedding_enqueue(photo)

                with tempfile.TemporaryDirectory() as temp_dir:
                    client = HttpClient(
                        "http://django.test/internal/photo-processing/v1",
                        "e2e-worker-token",
                        opener=self._open,
                    )
                    worker = Worker(
                        client,
                        WorkerConfig(
                            worker_build="pipeline-face-failure-e2e",
                            lease_seconds=120,
                            processor_identities=("1/face_embedding/1",),
                            temp_dir=Path(temp_dir),
                        ),
                    )
                    if failure_phase == "download":
                        failure = patch.object(
                            client,
                            "download",
                            side_effect=DownloadError(error_code, retryable=retryable),
                        )
                    else:
                        failure = patch(
                            "photo_worker.runner.extract_face_embeddings",
                            side_effect=FaceEmbeddingError(error_code),
                        )
                    with (
                        patch(
                            "processing.views.ExactObjectDownloadStorage.create_download_grant",
                            return_value=DownloadGrant(
                                url=_OBJECT_URL,
                                expires_at=timezone.now() + timedelta(seconds=30),
                            ),
                        ),
                        failure,
                    ):
                        self.assertIsNone(worker.run_once())

                attempt = ProcessingAttempt.objects.get(
                    photo=photo,
                    processor_type="face_embedding",
                )
                self.assertEqual(attempt.status, ProcessingAttempt.Status.FAILED)
                self.assertEqual(
                    (attempt.error_code, attempt.result["retryable"]),
                    (error_code, retryable),
                )

    def _open(self, request: Request, *, timeout: float) -> _Response:
        parsed = urlsplit(request.full_url)
        if request.full_url == _OBJECT_URL:
            return _Response(
                self.jpeg,
                {
                    "Content-Type": "image/jpeg",
                    "Content-Length": str(len(self.jpeg)),
                    "ETag": '"original-etag"',
                },
            )
        if request.full_url == _PREVIEW_UPLOAD_URL:
            assert request.get_method() == "PUT"
            assert self._preview_storage is not None
            assert self._preview_staging_key is not None
            upload_body = cast(BinaryIO, request.data)
            self._preview_storage.upload(
                staging_key=self._preview_staging_key, content=upload_body.read()
            )
            return _Response(b"", {})
        if getattr(self, "_make_completion_stale", False) and parsed.path.endswith("/complete"):
            self._make_completion_stale = False
            attempt_id = parsed.path.split("/")[-2]
            current = ProcessingAttempt.objects.get(pk=attempt_id)
            state = PhotoProcessingState.objects.get(
                photo_id=current.photo_id, processor_type="generate_preview"
            )
            successor = ProcessingAttempt.objects.create(
                event=current.event,
                run=current.run,
                job=current.job,
                photo=current.photo,
                contract_version=current.contract_version,
                processor_type=current.processor_type,
                processor_version=current.processor_version,
                configuration=current.configuration,
                input_fingerprint=current.input_fingerprint,
                claimed_at=timezone.now(),
                heartbeat_at=timezone.now(),
                lease_expires_at=timezone.now() + timedelta(seconds=30),
            )
            state.current_attempt = successor
            state.save(update_fields=["current_attempt", "updated_at"])
        response = self.client.generic(
            request.get_method(),
            parsed.path,
            data=request.data or b"",
            content_type=request.get_header("Content-type") or "application/json",
            HTTP_AUTHORIZATION=request.get_header("Authorization") or "",
        )
        body = bytes(response.content)
        if response.status_code >= 400:
            raise HTTPError(
                request.full_url,
                response.status_code,
                "Django API error",
                Message(),
                io.BytesIO(body),
            )
        return _Response(body, {key: value for key, value in response.items()})

    def _confirmed_preview_photo(self) -> Photo:
        batch = create_batch(
            uploader=self.user,
            event=self.event,
            data=BatchInput(expected_item_count=1),
        )
        registered = register_items(
            uploader=self.user,
            batch_id=batch.id,
            items=[
                ItemInput(
                    uuid4(),
                    "pipeline.jpg",
                    "image/jpeg",
                    len(self.jpeg),
                    folder_id=None,
                )
            ],
        )
        item_id = registered.items[0].id
        storage = _ConfirmationStorage()
        authorize_item(
            uploader=self.user,
            batch_id=batch.id,
            item_id=item_id,
            reason=AuthorizationReason.DATA_ATTEMPT,
            storage=storage,
        )
        item = UploadItem.objects.get(pk=item_id)
        storage.add(item.incoming_key, self.jpeg)
        photo = confirm_upload_item(
            uploader=self.user,
            batch_id=batch.id,
            item_id=item_id,
            storage=storage,
        )
        assert photo is not None
        return photo

    def _run_preview_worker(self, preview_storage: _PreviewStorage) -> None:
        self._preview_storage = preview_storage
        self._preview_staging_key = None
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = Worker(
                HttpClient(
                    "http://django.test/internal/photo-processing/v1",
                    "e2e-worker-token",
                    opener=self._open,
                ),
                WorkerConfig(
                    worker_build="pipeline-preview-e2e",
                    lease_seconds=120,
                    processor_identities=("2/generate_preview/1",),
                    temp_dir=Path(temp_dir),
                    log_secrets=("e2e-worker-token",),
                ),
            )
            with (
                patch(
                    "processing.views.ExactObjectDownloadStorage.create_download_grant",
                    return_value=DownloadGrant(
                        url=_OBJECT_URL, expires_at=timezone.now() + timedelta(seconds=30)
                    ),
                ),
                patch("processing.views.ExactPreviewStorage", return_value=preview_storage),
                patch(
                    "processing.services.previews.ExactPreviewStorage", return_value=preview_storage
                ),
            ):
                original_create_upload_grant = preview_storage.create_upload_grant

                def create_upload_grant(*, staging_key: str, max_ttl_seconds: int | None = None):
                    self._preview_staging_key = staging_key
                    return original_create_upload_grant(
                        staging_key=staging_key, max_ttl_seconds=max_ttl_seconds
                    )

                preview_storage.create_upload_grant = create_upload_grant  # type: ignore[method-assign]
                self.assertIsNone(worker.run_once())
        self._preview_storage = None

    @staticmethod
    def _jpeg_with_capture_time(capture_time: str = "2026:07:29 10:11:12") -> bytes:
        image = Image.new("RGB", (2000, 1000), "white")
        exif = Image.Exif()
        exif[36867] = capture_time
        output = io.BytesIO()
        image.save(output, format="JPEG", exif=exif)
        return output.getvalue()
