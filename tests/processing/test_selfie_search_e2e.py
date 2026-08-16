"""Real-model public selfie-search contract across the web, worker, and bearer boundaries."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from datetime import date, timedelta
from math import sqrt
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from photo_worker.client import HttpClient
from photo_worker.face_embedding import extract_selfie_embedding
from photo_worker.runner import Worker, WorkerConfig
from picflow.gallery import ResolvedPublicMedia
from picflow.models import Event, Photo
from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CONTRACT_VERSION,
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
    GENERATE_PREVIEW_PROCESSOR_VERSION,
    PREVIEW_CONTRACT_VERSION,
    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
    SCRFD_FACE_EMBEDDING_CONFIGURATION,
    request_generate_preview,
)
from processing.services.jobs import claim_job, complete_attempt
from processing.services.previews import complete_preview_attempt
from processing.storage import ObjectConflict, PreviewObject
from selfie_search.models import SelfieSearch, SelfieSearchAttempt, SelfieSearchResult
from selfie_search.storage import DownloadGrant, StoredTemporarySelfie

pytestmark = pytest.mark.face_models

_OBJECT_URL = "https://object.test/selfie.jpg?one-time-grant"
_MAX_INPUT_BYTES = 20 * 1024 * 1024


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


class _TemporarySelfieStorage:
    """A real temporary-object contract with only the remote S3 transport replaced."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    def put(self, *, key: str, content: bytes, content_type: str) -> StoredTemporarySelfie:
        self.objects[key] = (content, content_type)
        return StoredTemporarySelfie(key=key, size=len(content), content_type=content_type)

    def inspect(self, *, key: str) -> StoredTemporarySelfie:
        content, content_type = self.objects[key]
        return StoredTemporarySelfie(key=key, size=len(content), content_type=content_type)

    def create_download_grant(self, *, key: str, max_ttl_seconds: int) -> DownloadGrant:  # noqa: ARG002
        self.inspect(key=key)
        return DownloadGrant(url=_OBJECT_URL, expires_at=timezone.now() + timedelta(seconds=30))

    def delete(self, *, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class _PreviewPublicationStorage:
    """Exercise preview verification and promotion while keeping remote S3 out of the test."""

    def __init__(self, object: PreviewObject) -> None:
        self.object = object
        self.final_object: PreviewObject | None = None
        self.verified_keys: list[str] = []
        self.promoted_final_keys: list[str] = []

    def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
        self.verified_keys.append(key)
        assert self.object.byte_size <= max_bytes
        if key.startswith("derivatives/"):
            if self.final_object is None:
                from ingestion.storage import ObjectMissing

                raise ObjectMissing()
            return self.final_object
        return self.object

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject:
        assert source_etag == self.object.etag_wire
        if self.final_object is not None:
            raise ObjectConflict()
        self.final_object = self.object
        self.promoted_final_keys.append(final_key)
        return self.object


class _ReadableBody:
    def __init__(self, content: bytes) -> None:
        self._content = io.BytesIO(content)
        self.close_calls = 0

    def read(self, size: int | None = None) -> bytes:
        return self._content.read(-1 if size is None else size)

    def close(self) -> None:
        self.close_calls += 1
        self._content.close()


def _required_file(name: str) -> Path:
    value = os.environ.get(name)
    if value:
        path = Path(value)
        if path.is_file():
            return path
    pytest.skip(
        "requires local PHOTO_WORKER_SCRFD_MODEL_PATH, PHOTO_WORKER_SFACE_MODEL_PATH, "
        "and SELFIE_SEARCH_E2E_JPEG_PATH files"
    )


def _configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _nearby_vector(vector: tuple[float, ...]) -> list[float]:
    """Keep a known in-threshold, normalized second result independent of model internals."""
    basis_index = min(range(len(vector)), key=lambda index: abs(vector[index]))
    projection = vector[basis_index]
    orthogonal = [-projection * value for value in vector]
    orthogonal[basis_index] += 1.0
    orthogonal_norm = sqrt(sum(value * value for value in orthogonal))
    assert orthogonal_norm > 0.0
    orthogonal = [value / orthogonal_norm for value in orthogonal]
    cosine = 0.95
    sine = sqrt(1.0 - cosine * cosine)
    return [
        cosine * value + sine * normal for value, normal in zip(vector, orthogonal, strict=True)
    ]


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_FACE_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="selfie-search-e2e-worker-token",
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class SelfieSearchEndToEndTests(TestCase):
    """Catch a broken integration from accepted gallery evidence to paid-result delivery."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.scrfd_model = _required_file("PHOTO_WORKER_SCRFD_MODEL_PATH")
        cls.sface_model = _required_file("PHOTO_WORKER_SFACE_MODEL_PATH")
        cls.jpeg_path = _required_file("SELFIE_SEARCH_E2E_JPEG_PATH")
        super().setUpClass()

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="selfie-search-e2e-owner")
        self.event = self.make_event(slug="selfie-search-e2e", access_type=Event.AccessType.PAID)
        self.other_event = self.make_event(
            slug="selfie-search-e2e-other", access_type=Event.AccessType.FREE
        )
        self.jpeg = self.jpeg_path.read_bytes()
        self.storage = _TemporarySelfieStorage()

    def test_real_jpeg_reaches_stable_paid_result_without_widening_gallery_access(self) -> None:
        """Catch lost worker callbacks, unsafe cleanup, cross-event ranking, or paid-media leaks."""
        query = extract_selfie_embedding(
            self.jpeg_path,
            max_bytes=_MAX_INPUT_BYTES,
            content_type="image/jpeg",
            detection_threshold=0.5,
            scrfd_model_path=self.scrfd_model,
            sface_model_path=self.sface_model,
        )
        first_photo = self.add_accepted_photo(
            event=self.event,
            photo_id="1" * 32,
            vectors=[list(query.embedding), list(query.embedding)],
        )
        second_photo = self.add_accepted_preview_photo(
            event=self.event,
            photo_id="2" * 32,
            vectors=[_nearby_vector(query.embedding)],
        )
        self.add_accepted_photo(
            event=self.other_event,
            photo_id="3" * 32,
            vectors=[list(query.embedding)],
        )

        with patch("selfie_search.views.TemporarySelfieStorage", return_value=self.storage):
            submitted = self.client.post(
                reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug}),
                {"selfie": SimpleUploadedFile("query.jpg", self.jpeg, content_type="image/jpeg")},
            )

        self.assertEqual(submitted.status_code, 302)
        result_url = submitted["Location"]
        token = result_url.rstrip("/").rsplit("/", 1)[-1]
        search = SelfieSearch.objects.get(
            public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest()
        )
        self.assertEqual(search.eligible_photo_count, 2)
        self.assertEqual(search.eligible_face_count, 3)
        self.assertEqual((search.job.contract_version, search.job.processor_version), (1, 2))
        self.assertEqual(
            [
                (generation["contract_version"], generation["processor_version"])
                for generation in search.configuration["gallery_face_embedding_generations"]
            ],
            [
                (CONTRACT_VERSION, FACE_EMBEDDING_PROCESSOR_VERSION),
                (PREVIEW_CONTRACT_VERSION, PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION),
            ],
        )
        self.assertIn(search.temporary_object_key, self.storage.objects)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("processing.views.TemporarySelfieStorage", return_value=self.storage),
        ):
            worker = Worker(
                HttpClient(
                    "http://django.test/internal/photo-processing/v1",
                    "selfie-search-e2e-worker-token",
                    opener=self._open,
                ),
                WorkerConfig(
                    worker_build="selfie-search-e2e",
                    lease_seconds=120,
                    processor_types=("selfie_query",),
                    temp_dir=Path(temp_dir),
                    log_secrets=("selfie-search-e2e-worker-token",),
                ),
            )
            self.assertIsNone(worker.run_once())

        search.refresh_from_db()
        attempt = SelfieSearchAttempt.objects.get(job=search.job)
        rows = list(SelfieSearchResult.objects.filter(search=search).order_by("rank"))
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.matched_photo_count, 2)
        self.assertEqual(attempt.status, SelfieSearchAttempt.Status.SUCCEEDED)
        self.assertEqual([row.photo_id for row in rows], [first_photo.id, second_photo.id])
        self.assertEqual([row.rank for row in rows], [1, 2])
        self.assertNotIn(self.other_event.id, [row.photo.event_id for row in rows])
        self.assertEqual(self.storage.objects, {})
        self.assertEqual(len(self.storage.deleted), 1)
        self.assertEqual(search.temporary_object_key, "")
        self.assertIsNotNone(search.cleanup_confirmed_at)

        ready = self.client.get(result_url)
        reopened = self.client.get(result_url)
        self.assertEqual(ready.status_code, 200)
        self.assertContains(ready, "Возможные совпадения")
        self.assertEqual(
            [photo.photo_id for photo in ready.context["gallery_photos"]],
            [first_photo.id, second_photo.id],
        )
        self.assertEqual(
            [photo.photo_id for photo in reopened.context["gallery_photos"]],
            [first_photo.id, second_photo.id],
        )

        legacy_body = _ReadableBody(b"legacy-paid-original")
        preview_body = _ReadableBody(b"preview-paid-original")
        resolver = Mock()
        resolver.resolve.side_effect = [
            ResolvedPublicMedia(legacy_body, 20, "image/jpeg", "jpg"),
            ResolvedPublicMedia(preview_body, 21, "image/jpeg", "jpg"),
        ]
        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            legacy_result_media = self.client.get(
                reverse(
                    "selfie_search:result_media",
                    kwargs={
                        "event_slug": self.event.slug,
                        "public_token": token,
                        "photo_id": first_photo.id,
                        "variant": "preview-small",
                    },
                )
            )
            preview_result_media = self.client.get(
                reverse(
                    "selfie_search:result_media",
                    kwargs={
                        "event_slug": self.event.slug,
                        "public_token": token,
                        "photo_id": second_photo.id,
                        "variant": "preview-small",
                    },
                )
            )
            normal_legacy_gallery = self.client.get(
                reverse(
                    "photo_media",
                    kwargs={
                        "slug": self.event.slug,
                        "photo_id": first_photo.id,
                        "variant": "preview-small",
                    },
                )
            )
            normal_preview_gallery = self.client.get(
                reverse(
                    "photo_media",
                    kwargs={
                        "slug": self.event.slug,
                        "photo_id": second_photo.id,
                        "variant": "preview-small",
                    },
                )
            )

        self.assertEqual(legacy_result_media.status_code, 200)
        self.assertEqual(b"".join(legacy_result_media.streaming_content), b"legacy-paid-original")
        self.assertEqual(preview_result_media.status_code, 200)
        self.assertEqual(b"".join(preview_result_media.streaming_content), b"preview-paid-original")
        self.assertEqual(normal_legacy_gallery.status_code, 404)
        self.assertEqual(normal_preview_gallery.status_code, 404)
        self.assertEqual(legacy_body.close_calls, 1)
        self.assertEqual(preview_body.close_calls, 1)
        self.assertEqual(
            resolver.resolve.call_args_list,
            [
                call(photo=first_photo, variant="preview-small"),
                call(photo=second_photo, variant="preview-small"),
            ],
        )

    def make_event(self, *, slug: str, access_type: str) -> Event:
        return Event.objects.create(
            name=slug,
            slug=slug,
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            access_type=access_type,
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def add_accepted_photo(
        self,
        *,
        event: Event,
        photo_id: str,
        vectors: list[list[float]],
    ) -> Photo:
        configuration_hash = _configuration_hash(FACE_EMBEDDING_CONFIGURATION)
        input_fingerprint: dict[str, object] = {}
        photo = Photo.objects.create(
            id=photo_id,
            event=event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=len(self.jpeg),
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
        )
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
            input_fingerprint=input_fingerprint,
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            input_fingerprint=input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type="face_embedding",
            defaults={
                "status": PhotoProcessingState.Status.SUCCEEDED,
                "current_run": run,
                "current_job": job,
                "current_attempt": attempt,
                "accepted_attempt": attempt,
            },
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        for index, vector in enumerate(vectors):
            detection = PhotoFaceDetection.objects.create(
                artifact=artifact,
                attempt=attempt,
                face_index=index,
                status=PhotoFaceDetection.Status.KEPT,
            )
            FaceEmbedding.objects.create(
                detection=detection,
                model_version="sface",
                vector=vector,
                metadata={},
            )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration_hash=configuration_hash,
            accepted_attempt=attempt,
        )
        return photo

    def add_accepted_preview_photo(
        self, *, event: Event, photo_id: str, vectors: list[list[float]]
    ) -> Photo:
        """Publish a real accepted preview, then complete its production-enrolled 2/3 face job."""
        photo = Photo.objects.create(
            id=photo_id,
            event=event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=len(self.jpeg),
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        preview_state = request_generate_preview(photo, pixel_width=3200, pixel_height=2000)
        preview_claim = claim_job(
            contract_version=PREVIEW_CONTRACT_VERSION,
            processor_type="generate_preview",
            processor_version=GENERATE_PREVIEW_PROCESSOR_VERSION,
            worker_build="selfie-search-e2e-preview-fixture",
        )
        self.assertEqual(preview_claim.job.id, preview_state.current_job_id)
        preview_bytes = b"accepted-preview-fixture"
        preview_object = PreviewObject(
            etag_wire='"preview-etag"',
            etag_value="preview-etag",
            byte_size=len(preview_bytes),
            content_type="image/jpeg",
            sha256=hashlib.sha256(preview_bytes).hexdigest(),
            width=1600,
            height=1000,
        )
        preview_storage = _PreviewPublicationStorage(preview_object)
        preview_completion = complete_preview_attempt(
            preview_claim.attempt.id,
            result={
                "variant": "preview-small-v1",
                "content_type": "image/jpeg",
                "byte_size": preview_object.byte_size,
                "width": preview_object.width,
                "height": preview_object.height,
                "oriented_source_width": 3200,
                "oriented_source_height": 2000,
                "sha256": preview_object.sha256,
                "upload_ms": 4,
                "warnings": [],
            },
            storage=preview_storage,
        )
        self.assertFalse(preview_completion.idempotent)
        derivative = PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")
        preview_state.refresh_from_db()
        self.assertEqual(preview_state.accepted_attempt_id, derivative.accepted_attempt_id)
        self.assertEqual(preview_storage.promoted_final_keys, [derivative.final_key])
        self.assertIn(derivative.final_key, preview_storage.verified_keys)

        face_state = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        face_job = face_state.current_job
        assert face_job is not None
        self.assertEqual(
            (face_job.contract_version, face_job.processor_version),
            (PREVIEW_CONTRACT_VERSION, PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION),
        )
        self.assertEqual(face_job.configuration, SCRFD_FACE_EMBEDDING_CONFIGURATION)
        self.assertEqual(
            face_job.input_fingerprint,
            {
                "object_key": derivative.final_key,
                "object_size": derivative.byte_size,
                "object_content_type": derivative.content_type,
                "object_etag": None,
                "media_kind": derivative.variant,
                "pixel_width": derivative.width,
                "pixel_height": derivative.height,
            },
        )
        face_claim = claim_job(
            contract_version=PREVIEW_CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
            worker_build="selfie-search-e2e-face-fixture",
        )
        self.assertEqual(face_claim.job.id, face_job.id)
        completion = complete_attempt(
            face_claim.attempt.id,
            result={
                "model": "sface",
                "face_count": len(vectors),
                "faces": [
                    {
                        "index": index,
                        "bbox": [10 + index, 20, 30, 40],
                        "confidence": 0.9,
                        "landmarks": [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]],
                        "embedding": vector,
                    }
                    for index, vector in enumerate(vectors)
                ],
                "warnings": [],
                "input_geometry": {
                    "coordinate_space": derivative.variant,
                    "pixel_width": derivative.width,
                    "pixel_height": derivative.height,
                    "oriented_source_width": derivative.oriented_source_width,
                    "oriented_source_height": derivative.oriented_source_height,
                },
            },
        )
        self.assertFalse(completion.idempotent)
        face_state.refresh_from_db()
        self.assertEqual(face_state.status, PhotoProcessingState.Status.SUCCEEDED)
        self.assertEqual(face_state.accepted_attempt_id, face_claim.attempt.id)
        return photo

    def _open(self, request: Request, *, timeout: float) -> _Response:  # noqa: ARG002
        if request.full_url == _OBJECT_URL:
            return _Response(
                self.jpeg,
                {
                    "Content-Type": "image/jpeg",
                    "Content-Length": str(len(self.jpeg)),
                    "ETag": "",
                },
            )
        parsed = urlsplit(request.full_url)
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
