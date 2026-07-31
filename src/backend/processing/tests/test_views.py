import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from photo_worker.contracts import Claim
from picflow.models import Event, Photo
from selfie_search.models import SelfieSearch, SelfieSearchJob
from selfie_search.storage import DownloadGrant, StoredTemporarySelfie

from processing.contracts import AttemptCompletion
from processing.models import (
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services.enrollment import (
    GENERATE_PREVIEW_CONFIGURATION,
    request_capture_metadata,
    request_face_embedding_enqueue,
    request_processor,
)


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_FACE_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="worker-secret",
)
class WorkerApiTests(TestCase):
    """The production break caught here is letting a worker choose beyond its claimed attempt."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="worker-api-owner")
        self.event = Event.objects.create(
            name="Worker API event",
            slug="worker-api-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.headers = {"HTTP_AUTHORIZATION": "Bearer worker-secret"}

    def photo(
        self,
        identifier: str = "api-photo",
        *,
        original_key: str = "originals/0123456789abcdef0123456789abcdef",
    ) -> Photo:
        return Photo.objects.create(
            id=identifier,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=original_key,
            original_filename="photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def post(self, path: str, body: dict, **headers):
        return self.client.post(
            path,
            data=json.dumps(body),
            content_type="application/json",
            **(self.headers | headers),
        )

    def claim_body(self, **overrides: object) -> dict[str, object]:
        return {
            "contract_version": 1,
            "processor_type": "capture_metadata",
            "processor_version": 1,
            "worker_build": "worker-test",
            "lease_seconds": 120,
        } | overrides

    def face_claim_body(self, **overrides: object) -> dict[str, object]:
        return self.claim_body(processor_type="face_embedding", **overrides)

    def preview_claim_body(self, **overrides: object) -> dict[str, object]:
        return self.claim_body(
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            **overrides,
        )

    def terminal_body(self, job: dict[str, object], **overrides: object) -> dict[str, object]:
        failure_retryable = {
            "capture_metadata": {
                "decode_failed": False,
                "download_authorization_expired": True,
                "fingerprint_mismatch": False,
                "input_too_large": False,
                "network_interruption": True,
                "storage_unavailable": True,
                "unsupported_input": False,
            },
            "face_embedding": {
                "decode_failed": False,
                "download_authorization_expired": True,
                "fingerprint_mismatch": False,
                "input_too_large": False,
                "model_inference_error": False,
                "model_inference_timeout": True,
                "network_interruption": True,
                "no_face_detected": False,
                "storage_unavailable": True,
                "timeout": True,
                "all_faces_filtered": False,
                "unsupported_input": False,
            },
        }
        body = {
            "job_id": job["id"],
            "attempt_id": job["attempt_id"],
            "contract_version": 1,
            "processor_type": "capture_metadata",
            "processor_version": 1,
            "worker_build": "worker-test",
            "started_at": "2026-07-29T10:00:00Z",
            "finished_at": "2026-07-29T10:00:03Z",
            "download_ms": 1,
            "compute_ms": 2,
            "total_ms": 3,
            "outcome": "success",
            "result": {
                "capture_time": None,
                "source_field": None,
                "timezone_state": "not_applicable",
                "source_value": None,
                "warnings": ["capture_time_missing"],
            },
        } | overrides
        if body["outcome"] == "failure":
            body.pop("result")
            body["error_detail"] = "safe"
            if "retryable" not in body:
                body["retryable"] = failure_retryable.get(
                    str(body["processor_type"]),
                    {},
                ).get(str(body["error_code"]), False)
        return body

    def face_result_body(self, **overrides: object) -> dict[str, object]:
        return {
            "face_count": 1,
            "model": "sface",
            "faces": [
                {
                    "face_id": "face-1",
                    "bbox": [10, 10, 110, 110],
                    "quality": 0.93,
                    "embedding_sha256": "f" * 64,
                },
            ],
            "warnings": [],
        } | overrides

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_claim_grants_only_the_queued_job_and_unsupported_contract_polls_empty(
        self, grant
    ) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        photo = self.photo()
        request_capture_metadata(photo)

        unsupported = self.post(
            "/internal/photo-processing/v1/claim", self.claim_body(processor_version=2)
        )
        response = self.post("/internal/photo-processing/v1/claim", self.claim_body())

        self.assertEqual(unsupported.status_code, 200)
        self.assertEqual(unsupported.json(), {"empty": True, "suggested_delay_seconds": 5})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["empty"])
        self.assertEqual(payload["job"]["photo_id"], photo.id)
        self.assertEqual(payload["job"]["event_id"], str(self.event.id))
        self.assertEqual(payload["job"]["run_id"], str(ProcessingJob.objects.get().run_id))
        self.assertEqual(
            payload["job"]["download_url"], "https://storage.example.test/object?secret"
        )
        self.assertNotIn("object_key", payload["job"])
        self.assertEqual(grant.call_args.kwargs["final_key"], photo.original_key)
        self.assertGreaterEqual(grant.call_args.kwargs["max_ttl_seconds"], 1)
        self.assertLessEqual(grant.call_args.kwargs["max_ttl_seconds"], 119)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_deployed_worker_identity_claims_cross_the_django_to_worker_boundary(
        self, grant
    ) -> None:
        """Catch a claim JSON shape that makes the live worker stop before polling again."""
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_capture_metadata(self.photo("claim-contract-capture"))
        face_photo = self.photo(
            "claim-contract-face",
            original_key="originals/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        request_face_embedding_enqueue(face_photo)

        identities = (
            self.claim_body(processor_type="selfie_query"),
            self.face_claim_body(),
            self.claim_body(),
            self.preview_claim_body(),
            self.claim_body(
                contract_version=2,
                processor_type="face_embedding",
                processor_version=2,
            ),
        )

        for request in identities:
            response = self.post("/internal/photo-processing/v1/claim", request)

            self.assertEqual(response.status_code, 200)
            Claim.from_response(response.json())

    @patch("processing.views.ExactPreviewStorage")
    def test_preview_face_claim_grants_the_accepted_preview_derivative(
        self, preview_storage
    ) -> None:
        """A v2 face claim must grant its preview input, not validate it as an original."""
        preview_storage.return_value.create_download_grant.return_value.url = (
            "https://storage.example.test/preview?secret"
        )
        preview_storage.return_value.create_download_grant.return_value.expires_at = (
            timezone.now() + timedelta(seconds=30)
        )
        photo = self.photo("preview-face-claim")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        preview_state = request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )
        assert preview_state.current_job is not None
        preview_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=preview_state.current_job.run,
            job=preview_state.current_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=preview_state.current_job.configuration,
            input_fingerprint=preview_state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=(
                f"derivatives/previews/{photo.id}/preview-small-v1/"
                f"{preview_attempt.id}-{'a' * 64}.jpg"
            ),
            byte_size=1024,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=3200,
            oriented_source_height=2000,
            sha256="a" * 64,
            accepted_attempt=preview_attempt,
        )
        preview_state.status = PhotoProcessingState.Status.SUCCEEDED
        preview_state.accepted_attempt = preview_attempt
        preview_state.succeeded_at = timezone.now()
        preview_state.save(
            update_fields=["status", "accepted_attempt", "succeeded_at", "updated_at"]
        )
        request_face_embedding_enqueue(photo)

        response = self.post(
            "/internal/photo-processing/v1/claim",
            self.claim_body(
                contract_version=2,
                processor_type="face_embedding",
                processor_version=2,
            ),
        )

        self.assertEqual(response.status_code, 200)
        job = response.json()["job"]
        self.assertEqual(job["input_fingerprint"]["object_key"], derivative.final_key)
        self.assertEqual(job["download_url"], "https://storage.example.test/preview?secret")
        self.assertEqual(
            preview_storage.return_value.create_download_grant.call_args.kwargs["final_key"],
            derivative.final_key,
        )
        Claim.from_response(response.json())

        refreshed = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/download", {}
        )

        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(
            refreshed.json()["download_url"], "https://storage.example.test/preview?secret"
        )
        self.assertEqual(preview_storage.return_value.create_download_grant.call_count, 2)

    @patch("processing.views.ExactPreviewStorage.create_upload_grant")
    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_v2_preview_claim_has_one_exact_nonpersisted_upload_slot(
        self, download_grant, upload_grant
    ) -> None:
        download_grant.return_value.url = "https://storage.example.test/object?secret"
        download_grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        upload_grant.return_value.url = "https://storage.example.test/preview-put?secret"
        upload_grant.return_value.expires_at = timezone.now() + timedelta(seconds=20)
        photo = self.photo("preview-api-photo")
        request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )

        response = self.post("/internal/photo-processing/v1/claim", self.preview_claim_body())

        self.assertEqual(response.status_code, 200)
        slot = response.json()["job"]["output_slots"]
        self.assertEqual(len(slot), 1)
        self.assertEqual(
            slot[0],
            {
                "variant": "preview-small-v1",
                "upload_url": "https://storage.example.test/preview-put?secret",
                "upload_expires_at": upload_grant.return_value.expires_at.isoformat(),
                "content_type": "image/jpeg",
                "staging_key": (
                    f"processing-staging/previews/{response.json()['job']['attempt_id']}/"
                    "preview-small-v1.jpg"
                ),
                "max_bytes": 10_485_760,
                "max_width": 1600,
                "max_height": 1600,
                "checksum_algorithm": "sha256",
            },
        )
        self.assertEqual(upload_grant.call_args.kwargs["staging_key"], slot[0]["staging_key"])
        self.assertGreaterEqual(upload_grant.call_args.kwargs["max_ttl_seconds"], 1)
        self.assertLessEqual(upload_grant.call_args.kwargs["max_ttl_seconds"], 119)
        self.assertEqual(ProcessingAttempt.objects.get().result, {})

    @patch("processing.views.complete_preview_attempt")
    @patch("processing.views.ExactPreviewStorage.create_upload_grant")
    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_v2_preview_completion_accepts_only_the_bounded_preview_result(
        self, download_grant, upload_grant, complete_preview
    ) -> None:
        download_grant.return_value.url = "https://storage.example.test/object?secret"
        download_grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        upload_grant.return_value.url = "https://storage.example.test/preview-put?secret"
        upload_grant.return_value.expires_at = timezone.now() + timedelta(seconds=20)
        photo = self.photo("preview-result-photo")
        request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )
        job = self.post("/internal/photo-processing/v1/claim", self.preview_claim_body()).json()[
            "job"
        ]
        complete_preview.return_value = AttemptCompletion(
            attempt=ProcessingAttempt.objects.get(pk=job["attempt_id"])
        )
        result = {
            "variant": "preview-small-v1",
            "content_type": "image/jpeg",
            "byte_size": 1024,
            "width": 1600,
            "height": 1000,
            "oriented_source_width": 3200,
            "oriented_source_height": 2000,
            "sha256": "a" * 64,
            "upload_ms": 3,
            "warnings": ["color_profile_missing"],
        }

        rejected = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete",
            self.terminal_body(
                job,
                contract_version=2,
                processor_type="generate_preview",
                processor_version=1,
                result=result | {"width": 1601},
            ),
        )
        accepted = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete",
            self.terminal_body(
                job,
                contract_version=2,
                processor_type="generate_preview",
                processor_version=1,
                result=result,
            ),
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(rejected.status_code, 400)

    @patch("processing.views.complete_preview_attempt")
    @patch("processing.views.ExactPreviewStorage.create_upload_grant")
    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_preview_streaming_storage_failure_is_retryable_and_sanitized(
        self, download_grant, upload_grant, complete_preview
    ) -> None:
        download_grant.return_value.url = "https://storage.example.test/object?secret"
        download_grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        upload_grant.return_value.url = "https://storage.example.test/preview-put?secret"
        upload_grant.return_value.expires_at = timezone.now() + timedelta(seconds=20)
        photo = self.photo("preview-streaming-failure")
        request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )
        job = self.post("/internal/photo-processing/v1/claim", self.preview_claim_body()).json()[
            "job"
        ]
        complete_preview.side_effect = StorageUnavailable()
        result = {
            "variant": "preview-small-v1",
            "content_type": "image/jpeg",
            "byte_size": 1024,
            "width": 1600,
            "height": 1000,
            "oriented_source_width": 3200,
            "oriented_source_height": 2000,
            "sha256": "a" * 64,
            "upload_ms": 3,
            "warnings": [],
        }

        response = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete",
            self.terminal_body(
                job,
                contract_version=2,
                processor_type="generate_preview",
                processor_version=1,
                result=result,
            ),
        )

        self.assertEqual(
            (response.status_code, response.json()["error"]["code"]),
            (503, "storage_unavailable"),
        )
        self.assertNotIn("secret", response.content.decode())
        self.assertEqual(ProcessingAttempt.objects.get(pk=job["attempt_id"]).status, "in_progress")

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_completion_rejects_unknown_processor_type_or_version(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_capture_metadata(self.photo())
        job = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]
        attempt = job["attempt_id"]

        by_type = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete",
            self.terminal_body(job, processor_type="face_embedding"),
        )
        by_version = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete",
            self.terminal_body(job, processor_version=2),
        )

        self.assertEqual(by_type.status_code, 400)
        self.assertEqual(by_type.json()["error"]["code"], "invalid_result")
        self.assertEqual(by_version.status_code, 400)
        self.assertEqual(by_version.json()["error"]["code"], "invalid_result")

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_face_completion_payload_is_schema_validated(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_face_embedding_enqueue(self.photo())

        face_job = self.post("/internal/photo-processing/v1/claim", self.face_claim_body()).json()[
            "job"
        ]
        attempt = face_job["attempt_id"]
        invalid_face = self.terminal_body(
            face_job,
            processor_type="face_embedding",
            result={"face_count": 1, "model": "sface", "faces": [], "warnings": []},
        )

        invalid_complete = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete", invalid_face
        )
        valid_complete = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete",
            self.terminal_body(
                face_job, processor_type="face_embedding", result=self.face_result_body()
            ),
        )

        self.assertEqual(valid_complete.status_code, 200)
        self.assertEqual(invalid_complete.status_code, 400)
        self.assertEqual(invalid_complete.json()["error"]["code"], "invalid_result")

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_fail_rejects_unknown_processor_contract(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_capture_metadata(self.photo())
        job = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]
        attempt = job["attempt_id"]

        unsupported = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/fail",
            self.terminal_body(
                job,
                outcome="failure",
                processor_type="face_embedding",
                error_code="model_inference_error",
            ),
        )
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(unsupported.json()["error"]["code"], "invalid_result")

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_face_fail_rejects_unknown_error_code(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_face_embedding_enqueue(self.photo())
        face_job = self.post("/internal/photo-processing/v1/claim", self.face_claim_body()).json()[
            "job"
        ]
        attempt = face_job["attempt_id"]
        unsupported = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/fail",
            self.terminal_body(
                face_job,
                outcome="failure",
                processor_type="face_embedding",
                error_code="not_a_valid_code",
            ),
        )
        supported = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/fail",
            self.terminal_body(
                face_job,
                outcome="failure",
                processor_type="face_embedding",
                error_code="model_inference_error",
            ),
        )

        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(unsupported.json()["error"]["code"], "invalid_result")
        self.assertEqual(supported.status_code, 200)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_face_fail_rejects_retryability_not_produced_by_worker(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_face_embedding_enqueue(self.photo())
        face_job = self.post("/internal/photo-processing/v1/claim", self.face_claim_body()).json()[
            "job"
        ]
        attempt = face_job["attempt_id"]

        mismatched = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/fail",
            self.terminal_body(
                face_job,
                outcome="failure",
                processor_type="face_embedding",
                error_code="model_inference_error",
                retryable=True,
            ),
        )

        self.assertEqual(mismatched.status_code, 400)
        self.assertEqual(mismatched.json()["error"]["code"], "invalid_result")
        self.assertEqual(
            ProcessingAttempt.objects.get(pk=attempt).status,
            ProcessingAttempt.Status.IN_PROGRESS,
        )

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_claim_uses_immutable_fingerprint_when_live_photo_source_drifts(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=60)
        photo = self.photo()
        request_capture_metadata(photo)
        photo.original_key = "originals/ffffffffffffffffffffffffffffffff"
        photo.original_size = 999
        photo.original_content_type = "image/png"
        photo.save(update_fields=["original_key", "original_size", "original_content_type"])

        response = self.post("/internal/photo-processing/v1/claim", self.claim_body())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["job"]["input_limits"], {"max_bytes": 10, "content_type": "image/jpeg"}
        )
        self.assertEqual(
            grant.call_args.kwargs["final_key"], "originals/0123456789abcdef0123456789abcdef"
        )
        self.assertLessEqual(grant.call_args.kwargs["max_ttl_seconds"], 119)

    def test_rejects_unknown_fields_and_an_unauthenticated_request_before_claiming(self) -> None:
        photo = self.photo()
        request_capture_metadata(photo)

        invalid = self.post(
            "/internal/photo-processing/v1/claim", self.claim_body(photo_id="other")
        )
        denied = self.client.post(
            "/internal/photo-processing/v1/claim",
            data=json.dumps(self.claim_body()),
            content_type="application/json",
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(ProcessingJob.objects.get().status, ProcessingJob.Status.QUEUED)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_heartbeat_refresh_and_terminal_results_are_attempt_scoped_and_idempotent(
        self, grant
    ) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_capture_metadata(self.photo())
        claim = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]
        attempt = claim["attempt_id"]

        heartbeat = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/heartbeat", {"lease_seconds": 120}
        )
        refreshed = self.post(f"/internal/photo-processing/v1/attempts/{attempt}/download", {})
        success_body = self.terminal_body(claim)
        completed = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete", success_body
        )
        replay = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete", success_body
        )
        conflict = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete",
            success_body | {"total_ms": 4},
        )

        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(completed.json()["attempt"]["status"], "succeeded")
        self.assertTrue(replay.json()["idempotent"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "completion_conflict")
        self.assertEqual(ProcessingAttempt.objects.get().result["capture_time"], None)
        self.assertEqual(
            PhotoProcessingState.objects.get(photo_id="api-photo").status,
            PhotoProcessingState.Status.SUCCEEDED,
        )

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_retryable_and_permanent_failures_and_expired_refresh_have_stable_codes(
        self, grant
    ) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        request_capture_metadata(self.photo())
        attempt = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"][
            "attempt_id"
        ]

        job = ProcessingAttempt.objects.get(pk=attempt).job
        retry = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/fail",
            self.terminal_body(
                {"id": str(job.id), "attempt_id": attempt},
                outcome="failure",
                error_code="network_interruption",
            ),
        )
        expired = self.post(f"/internal/photo-processing/v1/attempts/{attempt}/download", {})

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["attempt"]["status"], "failed")
        self.assertEqual(expired.status_code, 409)
        self.assertEqual(expired.json()["error"]["code"], "lease_not_current")

    def test_methods_and_oversized_or_untyped_results_fail_without_model_field_selection(
        self,
    ) -> None:
        response = self.client.get("/internal/photo-processing/v1/claim", **self.headers)
        invalid = self.post(
            "/internal/photo-processing/v1/claim", self.claim_body(lease_seconds=True)
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")

    def test_machine_boundary_uses_json_for_method_malformed_identifier_and_size_rejections(
        self,
    ) -> None:
        method = self.client.get("/internal/photo-processing/v1/claim", **self.headers)
        malformed = self.post("/internal/photo-processing/v1/attempts/not-a-uuid/download", {})
        non_json = self.client.post(
            "/internal/photo-processing/v1/claim",
            data="x",
            content_type="text/plain",
            **self.headers,
        )
        oversized = self.client.post(
            "/internal/photo-processing/v1/claim",
            data="x" * 20_000,
            content_type="application/json",
            HTTP_CONTENT_LENGTH="20000",
            **self.headers,
        )

        self.assertEqual(
            (method.status_code, method.json()["error"]["code"]), (405, "method_not_allowed")
        )
        self.assertEqual(
            (malformed.status_code, malformed.json()["error"]["code"]), (404, "invalid_attempt_id")
        )
        self.assertEqual(
            (non_json.status_code, non_json.json()["error"]["code"]), (400, "invalid_request")
        )
        self.assertEqual(
            (oversized.status_code, oversized.json()["error"]["code"]), (400, "invalid_request")
        )

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_grant_failure_rolls_back_claim_and_expired_refresh_cannot_issue_a_url(
        self, grant
    ) -> None:
        request_capture_metadata(self.photo())
        grant.side_effect = StorageUnavailable()

        failed_claim = self.post("/internal/photo-processing/v1/claim", self.claim_body())

        self.assertEqual(
            (failed_claim.status_code, failed_claim.json()["error"]["code"]),
            (503, "storage_unavailable"),
        )
        self.assertEqual(ProcessingAttempt.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.get().status, ProcessingJob.Status.QUEUED)

        grant.side_effect = None
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=1)
        attempt = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"][
            "attempt_id"
        ]
        ProcessingAttempt.objects.filter(pk=attempt).update(lease_expires_at=timezone.now())

        expired = self.post(f"/internal/photo-processing/v1/attempts/{attempt}/download", {})

        self.assertEqual(
            (expired.status_code, expired.json()["error"]["code"]), (409, "lease_not_current")
        )

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_terminal_envelope_rejects_identity_code_and_secret_bypasses_without_persistence(
        self, grant
    ) -> None:
        grant.return_value.url = "https://storage.example.test/object?secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=60)
        request_capture_metadata(self.photo())
        job = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]
        attempt = job["attempt_id"]

        wrong_identity = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete",
            self.terminal_body(job, job_id="00000000-0000-0000-0000-000000000000"),
        )
        secret_source = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/complete",
            self.terminal_body(
                job,
                result={
                    "capture_time": "2026-07-29T10:00:00Z",
                    "source_field": "DateTimeOriginal",
                    "timezone_state": "explicit",
                    "source_value": "X-Amz-Signature=secret",
                    "warnings": [],
                },
            ),
        )
        permanent = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt}/fail",
            self.terminal_body(job, outcome="failure", error_code="unsupported_input"),
        )

        self.assertEqual(wrong_identity.json()["error"]["code"], "invalid_result")
        self.assertEqual(secret_source.json()["error"]["code"], "invalid_result")
        self.assertEqual(permanent.status_code, 200)
        stored = ProcessingAttempt.objects.get(pk=attempt)
        self.assertEqual(stored.error_detail, "The input is unsupported.")
        self.assertNotIn("secret", json.dumps(stored.result))
        self.assertNotIn("safe", json.dumps(stored.result))
        self.assertEqual(ProcessingLateReceipt.objects.count(), 0)
        self.assertNotIn("secret", json.dumps(EventProcessingRun.objects.get().report))

    def test_terminal_missing_attempt_maps_to_not_found(self) -> None:
        response = self.post(
            "/internal/photo-processing/v1/attempts/00000000-0000-0000-0000-000000000000/complete",
            {},
        )
        self.assertEqual(
            (response.status_code, response.json()["error"]["code"]), (404, "attempt_not_found")
        )

    def test_rejected_claim_build_equal_token_leaves_no_attempt(self) -> None:
        request_capture_metadata(self.photo())
        response = self.post(
            "/internal/photo-processing/v1/claim", self.claim_body(worker_build="worker-secret")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProcessingAttempt.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.get().status, ProcessingJob.Status.QUEUED)

    def test_rejected_claim_build_url_leaves_no_attempt(self) -> None:
        request_capture_metadata(self.photo())
        response = self.post(
            "/internal/photo-processing/v1/claim", self.claim_body(worker_build="https://x")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProcessingAttempt.objects.count(), 0)

    def test_all_worker_endpoints_reject_unsupported_methods_as_json(self) -> None:
        paths = [
            "/internal/photo-processing/v1/claim",
            "/internal/photo-processing/v1/attempts/not-a-uuid/heartbeat",
            "/internal/photo-processing/v1/attempts/not-a-uuid/download",
            "/internal/photo-processing/v1/attempts/not-a-uuid/complete",
            "/internal/photo-processing/v1/attempts/not-a-uuid/fail",
        ]
        for path in paths:
            for method in ("get", "put", "patch", "delete"):
                response = getattr(self.client, method)(path, **self.headers)
                self.assertEqual(
                    (response.status_code, response.json()["error"]["code"]),
                    (405, "method_not_allowed"),
                )

    def test_csrf_exempt_worker_post_reaches_json_validation(self) -> None:
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(
            "/internal/photo-processing/v1/claim",
            data="{}",
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)


class SelfieWorkerStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fail_delete = False

    def inspect(self, *, key: str) -> StoredTemporarySelfie:
        return StoredTemporarySelfie(key=key, size=1024, content_type="image/jpeg")

    def create_download_grant(self, *, key: str, max_ttl_seconds: int) -> DownloadGrant:
        assert key == "selfie-search/0123456789abcdef0123456789abcdef"
        assert max_ttl_seconds >= 1
        return DownloadGrant(
            url="https://storage.example.test/selfie?signature=secret",
            expires_at=timezone.now() + timedelta(seconds=30),
        )

    def delete(self, *, key: str) -> None:
        if self.fail_delete:
            raise StorageUnavailable()
        self.deleted.append(key)


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="worker-secret",
)
class SelfieWorkerApiTests(TestCase):
    """The production break caught here is a selfie attempt changing the photo worker wire shape."""

    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Selfie worker API",
            slug="selfie-worker-api",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="b" * 64,
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration={
                "embedding_model": "sface",
                "embedding_dimensions": 128,
                "cosine_distance_threshold": 0.363,
                "content_type": "image/jpeg",
                "content_size": 1024,
            },
        )
        SelfieSearchJob.objects.create(search=self.search, configuration=self.search.configuration)
        self.headers = {"HTTP_AUTHORIZATION": "Bearer worker-secret"}
        self.storage = SelfieWorkerStorage()

    def post(self, path: str, body: dict) -> HttpResponse:
        return self.client.post(
            path,
            data=json.dumps(body),
            content_type="application/json",
            **self.headers,
        )

    def claim_body(self) -> dict[str, object]:
        return {
            "contract_version": 1,
            "processor_type": "selfie_query",
            "processor_version": 1,
            "worker_build": "worker-test",
            "lease_seconds": 120,
        }

    def success_body(self, job: dict[str, object]) -> dict[str, object]:
        return {
            "job_id": job["id"],
            "attempt_id": job["attempt_id"],
            "contract_version": 1,
            "processor_type": "selfie_query",
            "processor_version": 1,
            "worker_build": "worker-test",
            "started_at": "2026-07-30T10:00:00Z",
            "finished_at": "2026-07-30T10:00:03Z",
            "download_ms": 1,
            "compute_ms": 2,
            "total_ms": 3,
            "outcome": "success",
            "result": {
                "model": "sface",
                "embedding": [1.0] + [0.0] * 127,
                "bbox": [1.0, 2.0, 32.0, 32.0],
                "confidence": 0.96,
                "landmarks": [[1.0, 2.0]] * 5,
                "timings": {
                    "decode_ms": 1,
                    "model_load_ms": 1,
                    "detect_ms": 1,
                    "embed_ms": 1,
                    "total_ms": 4,
                },
            },
        }

    @patch("processing.views.TemporarySelfieStorage")
    def test_selfie_claim_keeps_the_strict_worker_union_and_raw_uuid_callbacks(
        self, storage
    ) -> None:
        storage.return_value = self.storage

        claim = self.post("/internal/photo-processing/v1/claim", self.claim_body())
        self.assertEqual(claim.status_code, 200)
        job = claim.json()["job"]
        self.assertEqual(
            set(job),
            {
                "id",
                "attempt_id",
                "contract_version",
                "processor_type",
                "processor_version",
                "configuration",
                "search_id",
                "input_fingerprint",
                "input_limits",
                "lease_expires_at",
                "download_url",
                "download_expires_at",
            },
        )
        self.assertEqual(job["search_id"], str(self.search.id))
        self.assertNotIn("photo_id", job)
        self.assertEqual(
            job["input_fingerprint"]["temporary_key"],
            "selfie-search/0123456789abcdef0123456789abcdef",
        )
        self.assertNotIn("public_token", json.dumps(job))

        heartbeat = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/heartbeat",
            {"lease_seconds": 120},
        )
        refresh = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/download",
            {},
        )
        complete = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete",
            self.success_body(job),
        )

        self.assertEqual(
            (heartbeat.status_code, refresh.status_code, complete.status_code),
            (200, 200, 200),
        )
        self.assertEqual(complete.json()["attempt"]["status"], "succeeded")
        self.search.refresh_from_db()
        self.assertEqual(self.search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)
        self.assertEqual(self.search.temporary_object_key, "")
        self.assertEqual(self.storage.deleted, ["selfie-search/0123456789abcdef0123456789abcdef"])

    @patch("processing.views.TemporarySelfieStorage")
    def test_namespaced_selfie_reference_is_accepted_and_a_mixed_reference_fails_closed(
        self, storage
    ) -> None:
        storage.return_value = self.storage
        job = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]

        namespaced = self.post(
            f"/internal/photo-processing/v1/attempts/selfie_{job['attempt_id']}/heartbeat",
            {"lease_seconds": 120},
        )
        mixed = self.post(
            f"/internal/photo-processing/v1/attempts/photo_{job['attempt_id']}/heartbeat",
            {"lease_seconds": 120},
        )

        self.assertEqual(namespaced.status_code, 200)
        self.assertEqual(
            (mixed.status_code, mixed.json()["error"]["code"]),
            (404, "invalid_attempt_id"),
        )

    @patch("processing.views.TemporarySelfieStorage")
    def test_selfie_callback_rejects_a_non_normalized_query_without_state_change(
        self, storage
    ) -> None:
        storage.return_value = self.storage
        job = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]
        body = self.success_body(job)
        result = body["result"]
        assert isinstance(result, dict)
        result["embedding"] = [1.0] * 128

        response = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete",
            body,
        )
        self.search.refresh_from_db()

        self.assertEqual(
            (response.status_code, response.json()["error"]["code"]), (400, "invalid_result")
        )
        self.assertEqual(self.search.status, SelfieSearch.Status.PROCESSING)
        self.assertEqual(
            self.search.temporary_object_key,
            "selfie-search/0123456789abcdef0123456789abcdef",
        )
