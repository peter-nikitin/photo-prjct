"""Dedicated HTTP regressions for worker-boundary edge conditions."""

import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone
from selfie_search.models import SelfieSearch

from processing import views
from processing.models import (
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingConflictAudit,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services.enrollment import (
    request_capture_metadata,
    request_face_embedding_enqueue,
)
from processing.services.jobs import recover_expired_attempts
from processing.tests.test_views import SelfieWorkerApiTests, WorkerApiTests


class WorkerApiEdgeCases(WorkerApiTests):
    """Exercise edge cases through the worker HTTP endpoints, not service calls."""

    def setUp(self) -> None:
        super().setUp()
        self.factory = RequestFactory()

    def _grant(self, grant) -> None:
        grant.return_value.url = "https://storage.example.test/object?signature=secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=20)

    def _claim_one(self, grant) -> dict[str, object]:
        self._grant(grant)
        request_capture_metadata(self.photo())
        response = self.post("/internal/photo-processing/v1/claim", self.claim_body())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["empty"])
        return response.json()["job"]

    def _claim_face_one(self, grant) -> dict[str, object]:
        self._grant(grant)
        request_face_embedding_enqueue(self.photo())
        response = self.post("/internal/photo-processing/v1/claim", self.face_claim_body())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["empty"])
        return response.json()["job"]

    def _oversized_claim_request(self, content_length: str | None):
        raw = b"x" * (settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES + 2)
        request = self.factory.post(
            "/internal/photo-processing/v1/claim",
            data=raw,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )
        if content_length is None:
            request.META.pop("CONTENT_LENGTH", None)
        else:
            request.META["CONTENT_LENGTH"] = content_length
        # Supply the full stream independently of the deliberately untrusted header.
        request._stream = BytesIO(raw)  # noqa: SLF001
        return request

    def test_v2_face_result_rejects_more_than_its_frozen_face_or_embedding_limits(self) -> None:
        geometry = {
            "coordinate_space": "preview-small-v1",
            "pixel_width": 1600,
            "pixel_height": 1000,
            "oriented_source_width": 3200,
            "oriented_source_height": 2000,
        }
        face = {
            "index": 0,
            "bbox": [1.0, 2.0, 32.0, 32.0],
            "confidence": 0.9,
            "landmarks": [[1.0, 2.0]] * 5,
            "embedding": [0.007812500465661287] * 128,
        }

        too_many_faces = {
            "model": "sface",
            "face_count": 33,
            "faces": [face | {"index": index} for index in range(33)],
            "warnings": [],
            "input_geometry": geometry,
        }
        too_many_dimensions = {
            "model": "sface",
            "face_count": 1,
            "faces": [face | {"embedding": [0.007812500465661287] * 129}],
            "warnings": [],
            "input_geometry": geometry,
        }

        self.assertFalse(views._valid_face_embedding_result(too_many_faces, contract_version=2))
        self.assertFalse(
            views._valid_face_embedding_result(too_many_dimensions, contract_version=2)
        )

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_stale_completion_returns_stale_and_replay_is_idempotent(self, grant) -> None:
        job = self._claim_one(grant)
        attempt_id = job["attempt_id"]
        PhotoProcessingState.objects.filter(photo_id="api-photo").update(current_attempt=None)
        body = self.terminal_body(job)

        first = self.post(f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", body)
        second = self.post(f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", body)
        attempt = ProcessingAttempt.objects.get(pk=attempt_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["attempt"]["status"], ProcessingAttempt.Status.STALE)
        self.assertTrue(first.json()["stale"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["idempotent"])
        self.assertTrue(second.json()["stale"])
        self.assertEqual(attempt.status, ProcessingAttempt.Status.STALE)
        self.assertEqual(ProcessingLateReceipt.objects.count(), 0)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_stale_face_completion_returns_stale_and_replay_is_idempotent(self, grant) -> None:
        job = self._claim_face_one(grant)
        attempt_id = job["attempt_id"]
        PhotoProcessingState.objects.filter(photo_id="api-photo").update(current_attempt=None)
        body = self.terminal_body(
            job,
            processor_type="face_embedding",
            result=self.face_result_body(),
        )

        first = self.post(f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", body)
        second = self.post(f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", body)
        attempt = ProcessingAttempt.objects.get(pk=attempt_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["attempt"]["status"], ProcessingAttempt.Status.STALE)
        self.assertTrue(first.json()["stale"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["idempotent"])
        self.assertTrue(second.json()["stale"])
        self.assertEqual(attempt.status, ProcessingAttempt.Status.STALE)
        self.assertEqual(ProcessingLateReceipt.objects.count(), 0)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_expired_completion_before_recovery_creates_late_receipt(self, grant) -> None:
        job = self._claim_one(grant)
        attempt_id = job["attempt_id"]
        ProcessingAttempt.objects.filter(pk=attempt_id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", self.terminal_body(job)
        )
        attempt = ProcessingAttempt.objects.get(pk=attempt_id)
        receipt = ProcessingLateReceipt.objects.get(attempt=attempt)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["stale"])
        self.assertEqual(attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(receipt.attempt_id, attempt.id)
        self.assertNotIn("canonical_error_detail", receipt.payload)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_expired_completion_after_recovery_creates_late_receipt(self, grant) -> None:
        job = self._claim_one(grant)
        attempt_id = job["attempt_id"]
        ProcessingAttempt.objects.filter(pk=attempt_id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        recovered = recover_expired_attempts(now=timezone.now())

        response = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", self.terminal_body(job)
        )
        attempt = ProcessingAttempt.objects.get(pk=attempt_id)

        self.assertEqual([item.id for item in recovered], [attempt.id])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["stale"])
        self.assertEqual(attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertTrue(ProcessingLateReceipt.objects.filter(attempt=attempt).exists())

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_normal_claim_polling_recovers_and_later_reclaims_an_abandoned_attempt(
        self, grant
    ) -> None:
        job_payload = self._claim_one(grant)
        attempt_id = job_payload["attempt_id"]
        ProcessingAttempt.objects.filter(pk=attempt_id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        recovery_poll = self.post("/internal/photo-processing/v1/claim", self.claim_body())

        self.assertEqual(recovery_poll.status_code, 200)
        self.assertEqual(recovery_poll.json(), {"empty": True, "suggested_delay_seconds": 5})
        abandoned = ProcessingAttempt.objects.get(pk=attempt_id)
        retry_job = ProcessingJob.objects.get(pk=job_payload["id"])
        state = PhotoProcessingState.objects.get(photo_id=job_payload["photo_id"])
        self.assertEqual(abandoned.status, ProcessingAttempt.Status.EXPIRED)
        self.assertEqual(retry_job.status, ProcessingJob.Status.RETRY_WAIT)
        self.assertEqual(state.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertIsNotNone(retry_job.available_at)

        retry_at = retry_job.available_at
        grant.return_value.expires_at = retry_at + timedelta(seconds=20)
        with (
            patch("processing.services.jobs.timezone.now", return_value=retry_at),
            patch("processing.views.timezone.now", return_value=retry_at),
        ):
            retry_poll = self.post("/internal/photo-processing/v1/claim", self.claim_body())

        self.assertEqual(retry_poll.status_code, 200)
        self.assertFalse(retry_poll.json()["empty"])
        self.assertEqual(retry_poll.json()["job"]["id"], job_payload["id"])
        self.assertNotEqual(retry_poll.json()["job"]["attempt_id"], attempt_id)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_conflicting_failure_detail_creates_hash_only_audit(self, grant) -> None:
        job = self._claim_one(grant)
        attempt_id = job["attempt_id"]
        first = self.terminal_body(
            job,
            outcome="failure",
            error_code="unsupported_input",
            error_detail="first worker detail",
        )
        conflicting = first | {"error_detail": "conflicting worker detail"}

        accepted = self.post(f"/internal/photo-processing/v1/attempts/{attempt_id}/fail", first)
        rejected = self.post(
            f"/internal/photo-processing/v1/attempts/{attempt_id}/fail", conflicting
        )
        audit = ProcessingConflictAudit.objects.get(attempt_id=attempt_id)
        stored = ProcessingAttempt.objects.get(pk=attempt_id)
        audit_values = json.dumps(
            {
                "attempt_id": str(audit.attempt_id),
                "event_id": str(audit.event_id),
                "job_id": str(audit.job_id),
                "received_at": audit.received_at.isoformat(),
                "submitted_hash": audit.submitted_hash,
                "code": audit.code,
            }
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()["error"]["code"], "completion_conflict")
        self.assertEqual(audit.code, "terminal_conflict")
        self.assertEqual(len(audit.submitted_hash), 64)
        self.assertNotEqual(audit.submitted_hash, stored.result_hash)
        self.assertNotIn("first worker detail", audit_values)
        self.assertNotIn("conflicting worker detail", audit_values)
        self.assertNotIn("worker-secret", audit_values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcessingConflictAudit.objects.filter(pk=audit.pk).update(code="rewritten")

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_worker_timestamps_persist_separately_from_exact_result(self, grant) -> None:
        job = self._claim_one(grant)
        attempt_id = job["attempt_id"]
        body = self.terminal_body(job)

        response = self.post(f"/internal/photo-processing/v1/attempts/{attempt_id}/complete", body)
        attempt = ProcessingAttempt.objects.get(pk=attempt_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["attempt"]["status"], ProcessingAttempt.Status.SUCCEEDED)
        self.assertEqual(
            attempt.result,
            {
                "capture_time": None,
                "source_field": None,
                "timezone_state": "not_applicable",
                "source_value": None,
                "source_offset": None,
                "event_timezone": "Europe/Moscow",
                "warnings": ["capture_time_missing"],
            },
        )
        self.assertEqual(attempt.worker_started_at.isoformat(), "2026-07-29T10:00:00+00:00")
        self.assertEqual(attempt.worker_finished_at.isoformat(), "2026-07-29T10:00:03+00:00")
        self.assertNotIn("worker_started_at", attempt.result)
        self.assertNotIn("worker_finished_at", attempt.result)

    def test_absent_content_length_reads_at_most_limit_plus_one(self) -> None:
        request = self._oversized_claim_request(content_length=None)

        with patch.object(request, "read", wraps=request.read) as read:
            response = views.claim(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)["error"]["code"], "invalid_request")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args, (settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES + 1,))

    def test_falsely_small_content_length_reads_at_most_limit_plus_one(self) -> None:
        request = self._oversized_claim_request(content_length="2")

        with patch.object(request, "read", wraps=request.read) as read:
            response = views.claim(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)["error"]["code"], "invalid_request")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args, (settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES + 1,))

    def test_rejected_build_token_has_no_attempt_report_or_log(self) -> None:
        request_capture_metadata(self.photo())

        with self.assertNoLogs("processing", level="INFO"):
            response = self.post(
                "/internal/photo-processing/v1/claim", self.claim_body(worker_build="worker-secret")
            )
        report = EventProcessingRun.objects.get().report

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertEqual(ProcessingAttempt.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.get().status, ProcessingJob.Status.QUEUED)
        self.assertNotIn("worker-secret", json.dumps(report))

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_post_sign_lease_expiry_returns_no_url(self, grant) -> None:
        self._grant(grant)
        grant.return_value.expires_at = timezone.now() + timedelta(days=1)
        request_capture_metadata(self.photo())

        response = self.post("/internal/photo-processing/v1/claim", self.claim_body())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "lease_not_current")
        self.assertNotIn("download_url", response.content.decode())
        self.assertNotIn("signature=secret", response.content.decode())
        self.assertEqual(ProcessingAttempt.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.get().status, ProcessingJob.Status.QUEUED)

    @patch("processing.views.ExactObjectDownloadStorage.create_download_grant")
    def test_signed_url_never_reaches_logs_or_models(self, grant) -> None:
        self._grant(grant)
        request_capture_metadata(self.photo())

        with self.assertNoLogs("processing", level="INFO"):
            response = self.post("/internal/photo-processing/v1/claim", self.claim_body())
        attempt = ProcessingAttempt.objects.get()
        job = ProcessingJob.objects.get()
        run = EventProcessingRun.objects.get()
        durable = json.dumps(
            {
                "attempt": attempt.input_fingerprint,
                "job": job.input_fingerprint,
                "report": run.report,
            },
            sort_keys=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["job"]["download_url"],
            "https://storage.example.test/object?signature=secret",
        )
        self.assertNotIn("signature=secret", durable)
        self.assertNotIn("https://storage.example.test", durable)
        self.assertNotIn("worker-secret", durable)


class SelfieWorkerApiEdgeCases(SelfieWorkerApiTests):
    """The production break caught here is terminal publication before exact selfie deletion."""

    @patch("processing.views.TemporarySelfieStorage")
    def test_cleanup_storage_failure_is_retryable_and_an_identical_callback_finalizes_once(
        self, storage
    ) -> None:
        storage.return_value = self.storage
        job = self.post("/internal/photo-processing/v1/claim", self.claim_body()).json()["job"]
        body = self.success_body(job)
        self.storage.fail_delete = True

        pending = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body
        )
        self.search.refresh_from_db()

        self.assertEqual(
            (pending.status_code, pending.json()["error"]["code"]),
            (503, "storage_unavailable"),
        )
        self.assertEqual(self.search.status, SelfieSearch.Status.CLEANUP_PENDING)
        self.assertEqual(
            self.search.temporary_object_key,
            "selfie-search/0123456789abcdef0123456789abcdef",
        )

        self.storage.fail_delete = False
        replay = self.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body
        )
        self.search.refresh_from_db()

        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["idempotent"])
        self.assertEqual(self.search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)
        self.assertEqual(self.search.temporary_object_key, "")
        self.assertEqual(self.storage.deleted, ["selfie-search/0123456789abcdef0123456789abcdef"])
