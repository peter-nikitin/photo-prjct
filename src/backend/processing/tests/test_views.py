import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services.enrollment import request_capture_metadata


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
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

    def photo(self, identifier: str = "api-photo") -> Photo:
        return Photo.objects.create(
            id=identifier,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/0123456789abcdef0123456789abcdef",
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

    def terminal_body(self, job: dict[str, object], **overrides: object) -> dict[str, object]:
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
            body |= {
                "retryable": body["error_code"] == "network_interruption",
                "error_detail": "safe",
            }
        return body

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
