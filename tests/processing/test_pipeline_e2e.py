"""Real-JPEG proof across the Django worker API and standalone worker iteration."""

from __future__ import annotations

import io
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo
from PIL import Image
from processing.models import EventProcessingRun, PhotoProcessingState, ProcessingAttempt
from processing.services.enrollment import request_capture_metadata
from processing.storage import DownloadGrant

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "worker"))

from photo_worker.client import HttpClient  # noqa: E402
from photo_worker.runner import Worker, WorkerConfig  # noqa: E402

_OBJECT_URL = "https://object.test/original.jpg?one-time-grant"


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


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
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
        )
        self.jpeg = self._jpeg_with_capture_time()

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
                "warnings": ["capture_time_malformed", "capture_time_missing"],
            },
        )

    def _open(self, request: Request, *, timeout: float) -> _Response:
        parsed = urlsplit(request.full_url)
        if request.full_url == _OBJECT_URL:
            return _Response(
                self.jpeg,
                {
                    "Content-Type": "image/jpeg",
                    "Content-Length": str(len(self.jpeg)),
                    "ETag": "",
                },
            )
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
                request.full_url, response.status_code, "Django API error", {}, io.BytesIO(body)
            )
        return _Response(body, {key: value for key, value in response.items()})

    @staticmethod
    def _jpeg_with_capture_time(capture_time: str = "2026:07:29 10:11:12") -> bytes:
        image = Image.new("RGB", (4, 3), "white")
        exif = Image.Exif()
        exif[36867] = capture_time
        output = io.BytesIO()
        image.save(output, format="JPEG", exif=exif)
        return output.getvalue()
