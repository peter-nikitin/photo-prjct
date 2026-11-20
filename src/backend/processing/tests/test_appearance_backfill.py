import json
from datetime import timedelta
from unittest.mock import patch

import psycopg
from config.queue_telemetry_protocol import CHANNEL
from config.queue_work import PHOTO
from config.tests.test_queue_work import work
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from photo_worker.contracts import Claim
from photo_worker.runner import _success_payload, _timestamp

from processing.models import PhotoProcessingState, ProcessingAttempt, ProcessingJob
from processing.services.previews import complete_preview_attempt
from processing.tests.test_appearance import appearance_result
from processing.tests.test_previews import FakePreviewStorage, _PreviewPublicationFixture


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="appearance-worker-secret",
    PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False,
)
class AppearanceBackfillTests(_PreviewPublicationFixture, TestCase):
    def publish(self, identifier):
        photo, claimed = self._claim(identifier)
        stored = self._stored_object()
        with self.captureOnCommitCallbacks(execute=True):
            complete_preview_attempt(
                claimed.attempt.pk,
                result=self._result(stored),
                storage=FakePreviewStorage(stored),
            )
        return photo

    def session(self, limit=1, processes=1):
        from processing.services.appearance_backfill import AppearanceBackfill

        return AppearanceBackfill(event_id=self.event.pk, limit=limit, processes=processes, seed=7)

    def worker_post(self, url, body):
        return self.client.post(
            url,
            body,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer appearance-worker-secret",
        )

    @patch("processing.views.ExactPreviewStorage.create_download_grant")
    def test_backfill_leases_only_new_manifest_jobs_and_uses_existing_callback(self, grant):
        grant.return_value.url = "https://storage.example.test/private?signature=secret"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        fresh = self.publish("new-preview")
        old = self.publish("old-preview")
        waiting = self.publish("older-queued-preview")
        flag = FeatureFlag.objects.create(key="person-appearance-processing", state="staff")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="off")

        from processing.services.person_appearance import request_person_appearance

        for photo in (old, waiting):
            preview = photo.derivatives.get(variant="preview-small-v1")
            assert request_person_appearance(photo.pk, preview.final_key, staff_rehearsal=True)
        body = {
            "contract_version": 2,
            "processor_type": "person_appearance",
            "processor_version": 1,
            "lease_seconds": 120,
        }
        old_claim = Claim.from_response(self.worker_post(reverse("processing_claim"), body).json())
        assert old_claim.job is not None and old_claim.job.photo_id == old.pk

        def complete(job):
            now = _timestamp()
            payload = _success_payload(job, now, now, appearance_result(), 1, 2, 3)
            response = self.worker_post(
                reverse("processing_complete", args=[job.attempt_id]), payload
            )
            assert response.status_code == 200, response.json()

        complete(old_claim.job)
        flag.state = "off"
        flag.save(update_fields=["state"])

        session = self.session()
        manifest = session.enroll()
        assert [row["photo_id"] for row in manifest["manifest"]] == [fresh.pk]
        assert "signature" not in str(manifest)
        claim_response = self.worker_post(reverse("processing_claim"), body)
        assert claim_response.json()["empty"] is True
        claim = Claim.from_response(session.claim())
        assert claim.job is not None and claim.job.photo_id == fresh.pk
        complete(claim.job)
        result = session.result(claim.job.attempt_id)
        assert result["accepted"] is True
        assert result["compute_ms"] == 2
        finish = session.finish()
        assert finish["jobs"] == {"succeeded": 1}
        assert finish["pointers_preserved"] is True
        assert old.processing_states.get(
            processor_type="person_appearance"
        ).accepted_attempt_id == (ProcessingAttempt.objects.get(pk=old_claim.job.attempt_id).pk)
        assert waiting.processing_states.get(processor_type="person_appearance").status == "queued"
        assert not ProcessingAttempt.objects.filter(
            photo=waiting, processor_type="person_appearance"
        ).exists()

    def test_backfill_rejects_insufficient_current_previews_without_partial_enrollment(self):
        from processing.services.appearance_backfill import BackfillError

        self.publish("usable-preview")
        stale = self.publish("stale-preview")
        PhotoProcessingState.objects.filter(photo=stale, processor_type="generate_preview").update(
            accepted_attempt=None
        )
        with self.assertRaisesRegex(BackfillError, "insufficient_previews"):
            self.session(limit=2).enroll()
        assert not ProcessingJob.objects.filter(processor_type="person_appearance").exists()

    @patch("processing.views.ExactPreviewStorage.create_download_grant")
    def test_backfill_releases_unreported_leases_after_worker_drain(self, grant):
        from processing.services.appearance_backfill import BackfillError

        grant.return_value.url = "https://storage.example.test/private"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        self.publish("fresh-preview-a")
        self.publish("fresh-preview-b")
        session = self.session(limit=2)
        session.enroll()
        claim = Claim.from_response(session.claim())
        assert claim.job is not None
        with self.assertRaisesRegex(BackfillError, "pool_full"):
            session.claim()
        with self.assertRaisesRegex(BackfillError, "unknown_attempt"):
            session.result("outside-manifest")
        finish = session.finish()
        assert finish["jobs"] == {"retry_wait": 1, "queued": 1}
        assert len(finish["remaining"]) == 2
        assert ProcessingAttempt.objects.get(pk=claim.job.attempt_id).status == "failed"

    def test_backfill_requires_both_gates_off_before_enrollment(self):
        from processing.services.appearance_backfill import BackfillError

        self.publish("fresh-before-gates")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="staff")
        with self.assertRaisesRegex(BackfillError, "appearance_gates_enabled"):
            self.session().enroll()
        assert not ProcessingJob.objects.filter(processor_type="person_appearance").exists()

    def test_django_command_keeps_stdout_to_jsonl(self):
        from io import BytesIO, StringIO
        from types import SimpleNamespace

        from django.core.management import call_command

        self.publish("ready-to-enroll")
        stdin = SimpleNamespace(
            buffer=BytesIO(b'{"op":"enroll"}\n{"op":"finish","release":true}\n')
        )
        stdout = StringIO()
        with patch("sys.stdin", stdin):
            call_command(
                "run_person_appearance_backfill_session",
                event_id=self.event.pk,
                limit=1,
                processes=1,
                seed=7,
                stdout=stdout,
            )
        ready, enrollment, final = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert ready == {"ready": True}
        assert enrollment["manifest"][0]["photo_id"] == "ready-to-enroll"
        assert final["jobs"] == {"queued": 1}
        assert final["flags"]["person-appearance-processing"] == "off"

    def test_session_stops_new_leases_when_gate_changes_after_enrollment(self):
        from processing.services.appearance_backfill import BackfillError

        self.publish("fresh-before-gate-change")
        session = self.session()
        session.enroll()
        FeatureFlag.objects.update_or_create(
            key="apparel-search-expansion", defaults={"state": "on"}
        )
        with self.assertRaisesRegex(BackfillError, "appearance_gates_enabled"):
            session.claim()
        assert session.finish()["flags"]["apparel-search-expansion"] == "on"
        assert not ProcessingAttempt.objects.filter(processor_type="person_appearance").exists()


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="appearance-worker-secret",
    PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False,
)
class AppearanceBackfillQueueWorkTests(_PreviewPublicationFixture, TransactionTestCase):
    session = AppearanceBackfillTests.session
    worker_post = AppearanceBackfillTests.worker_post

    @patch("processing.views.ExactPreviewStorage.create_download_grant")
    def test_backfill_uses_shared_queue_counters_and_emits_one_completion(self, grant):
        grant.return_value.url = "https://storage.example.test/private"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        _, preview_claim = self._claim("backfill-queue-work")
        stored = self._stored_object()
        complete_preview_attempt(
            preview_claim.attempt.pk,
            result=self._result(stored),
            storage=FakePreviewStorage(stored),
        )

        processor = "person_appearance"
        with psycopg.connect(**connection.get_connection_params(), autocommit=True) as listener:
            listener.execute(f"LISTEN {CHANNEL}")
            session = self.session()
            session.enroll()
            assert work(PHOTO, processor) == (1, 0, 0, 0)
            claim = Claim.from_response(session.claim())
            assert claim.job is not None
            assert work(PHOTO, processor) == (0, 1, 0, 0)
            now = _timestamp()
            result = _success_payload(claim.job, now, now, appearance_result(), 1, 2, 3)
            url = reverse("processing_complete", args=[claim.job.attempt_id])
            response = self.worker_post(url, result)
            assert response.status_code == 200, response.json()
            assert session.result(claim.job.attempt_id)["accepted"] is True
            duplicate = self.worker_post(url, result)
            assert duplicate.status_code == 200 and duplicate.json()["idempotent"] is True
            assert work(PHOTO, processor) == (0, 0, 0, 0)
            [notification] = list(listener.notifies(timeout=0.2))

        event = json.loads(notification.payload)
        assert event["event"] == "queue.task.completed"
        assert event["queue"] == "processing_person_appearance"
        assert set(event["segments_ms"]) == {"waiting", "processing"}
