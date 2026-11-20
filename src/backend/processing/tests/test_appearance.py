import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from photo_worker.contracts import Claim, InputFingerprint
from photo_worker.runner import _assert_terminal_size, _success_payload, _timestamp

from processing.appearance import appearance_metadata, valid_appearance_result
from processing.contracts import ClaimedJob
from processing.models import PersonAppearanceDetection, PhotoDerivative, PhotoProcessingState
from processing.services.jobs import claim_job
from processing.services.previews import complete_preview_attempt
from processing.tests import test_previews


def appearance_result(*rows, head_count=0):
    return {
        "people": list(rows),
        "head_count": head_count,
        "detections_truncated": False,
        "descriptors_truncated": False,
    }


def maximum_result():
    row = {
        "bbox": [1000, 100, 1400, 700],
        "confidence": 0.987654,
        "reason": "",
        "focus": [81234.123456, 81234.123456, 100.123456, 100.123456],
        "head": [1100.123456, 150.123456, 1160.123456, 230.123456],
        "reid": [-0.062499] * 256,
        "color": [0.051031] * 384,
        "helmet": [0.023809] * 1764,
    }
    excess = {k: v for k, v in row.items() if k not in {"reid", "color", "helmet"}}
    return {
        "people": [row] * 8 + [excess | {"reason": "descriptor_limit"}] * 24,
        "head_count": 32,
        "detections_truncated": True,
        "descriptors_truncated": True,
    }


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="appearance-worker-secret",
    PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False,
)
class AppearanceContractTests(test_previews._PreviewPublicationFixture, TestCase):
    def worker_post(self, url, body):
        return self.client.post(
            url,
            json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer appearance-worker-secret",
        )

    def publish(self, photo_id):
        photo, claimed = self._claim(photo_id)
        assert isinstance(claimed, ClaimedJob)
        stored = self._stored_object()
        with self.captureOnCommitCallbacks(execute=True):
            complete_preview_attempt(
                claimed.attempt.pk,
                result=self._result(stored),
                storage=test_previews.FakePreviewStorage(stored),
            )
        return photo, PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")

    @patch("processing.views.ExactPreviewStorage.create_download_grant")
    def test_post_commit_exact_preview_through_worker_transport_and_persistence(self, grant):
        even_without_flag, _ = self.publish("no-appearance-by-default")
        assert not even_without_flag.processing_states.filter(
            processor_type="person_appearance"
        ).exists()
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        photo, preview = self.publish("appearance-accepted-input")
        grant.return_value.url = "https://storage.example.test/exact-preview"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        claimed = self.worker_post(
            reverse("processing_claim"),
            {
                "contract_version": 2,
                "processor_type": "person_appearance",
                "processor_version": 1,
                "lease_seconds": 120,
            },
        )
        assert claimed.status_code == 200, claimed.json()
        assert grant.call_count == 1
        assert grant.call_args.kwargs["final_key"] == preview.final_key
        assert 0 < grant.call_args.kwargs["max_ttl_seconds"] <= 120
        assert (job := Claim.from_response(claimed.json()).job) is not None
        assert isinstance(job.input_fingerprint, InputFingerprint)
        assert job.input_fingerprint.object_key == preview.final_key
        grant.reset_mock()
        refresh_url = reverse("processing_download", args=[job.attempt_id])
        assert self.worker_post(refresh_url, {}).status_code == 200
        assert grant.call_args.kwargs["final_key"] == preview.final_key
        attempt_url = reverse("processing_complete", args=[job.attempt_id])
        result = maximum_result()
        assert valid_appearance_result(result)
        now = _timestamp()
        payload = _success_payload(job, now, now, result, 0, 0, 0)
        _assert_terminal_size(payload, 384 * 1024)
        assert len(json.dumps(payload).encode()) < 384 * 1024
        assert len(json.dumps(appearance_metadata(result)).encode()) < 128 * 1024
        malformed = result | {"head_count": 0}
        rejection = self.worker_post(attempt_url, payload | {"result": malformed})
        assert rejection.status_code == 400, rejection.json()
        response = self.worker_post(attempt_url, payload)
        assert response.status_code == 200, response.json()
        assert self.worker_post(attempt_url, payload).json()["idempotent"]
        state = photo.processing_states.get(processor_type="person_appearance")
        assert state.accepted_attempt_id is not None
        assert state.accepted_attempt.result == appearance_metadata(result)
        assert (
            PersonAppearanceDetection.objects.filter(attempt=state.accepted_attempt).count() == 32
        )
        assert (
            PersonAppearanceDetection.objects.filter(
                attempt=state.accepted_attempt, reid__isnull=False
            ).count()
            == 8
        )

    def test_staff_gate_accepts_one_explicit_photo_and_checks_accepted_pointer(self):
        from django.core.management import call_command

        FeatureFlag.objects.create(key="person-appearance-processing", state="staff")
        photo, preview = self.publish("operator-appearance-preview")
        assert not photo.processing_states.filter(processor_type="person_appearance").exists()
        call_command("enroll_person_appearance", photo.pk, preview.final_key)
        PhotoProcessingState.objects.filter(photo=photo, processor_type="generate_preview").update(
            accepted_attempt=None
        )
        claim = claim_job(
            contract_version=2, processor_type="person_appearance", processor_version=1
        )
        assert claim.empty
        assert photo.processing_states.get(processor_type="person_appearance").status == "cancelled"
