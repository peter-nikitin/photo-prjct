from __future__ import annotations

import hashlib
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo
from processing.contracts import ClaimedJob
from processing.models import BibReading, PhotoProcessingState
from processing.services.bibs import BIB_INFERENCE_CONFIGURATION_SHA256
from processing.services.enrollment import request_bib_recognition
from processing.services.jobs import claim_job


@override_settings(PHOTO_PROCESSING_WORKER_TOKEN="worker-secret")
class BibPipelineEndToEndTests(TestCase):
    def test_applicable_published_photo_flows_from_enrollment_to_exact_projection(self) -> None:
        owner = get_user_model().objects.create_user(username="bib-e2e-owner")
        event = Event.objects.create(
            name="Bib e2e event",
            slug="bib-e2e-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        photo = Photo.objects.create(
            id="bib-e2e-photo",
            event=event,
            src="",
            uploaded_by=owner,
            original_key="originals/bib-e2e-photo",
            original_filename="e2e.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1,
        )
        request_bib_recognition(photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="bib_recognition",
            processor_version=1,
            worker_build="bib-e2e-worker",
        )
        assert isinstance(claimed, ClaimedJob)
        source_sha256 = "e" * 64
        number = "007"
        candidate_id = hashlib.sha256(f"{source_sha256}\0{number}".encode()).hexdigest()[:24]
        result = {
            "source_sha256": source_sha256,
            "configuration_sha256": BIB_INFERENCE_CONFIGURATION_SHA256,
            "width": 1000,
            "height": 800,
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "number": number,
                    "raw_text": number,
                    "confidence": 0.9,
                    "polygon": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]],
                    "supporting_region_count": 1,
                    "crop": [0, 0, 384, 384],
                    "visual": {
                        "status": "complete",
                        "raw_response": '{"numbers":["007"]}',
                        "numbers": [number],
                        "error_code": None,
                        "inference_ms": 4.0,
                    },
                }
            ],
            "tile_count": 1,
            "ocr_preparation_ms": 1.0,
            "ocr_inference_ms": 2.0,
        }
        now = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        response = self.client.post(
            f"/internal/photo-processing/v1/attempts/{claimed.attempt.id}/complete",
            data=json.dumps(
                {
                    "job_id": str(claimed.job.id),
                    "attempt_id": str(claimed.attempt.id),
                    "contract_version": 1,
                    "processor_type": "bib_recognition",
                    "processor_version": 1,
                    "worker_build": "bib-e2e-worker",
                    "started_at": now,
                    "finished_at": now,
                    "download_ms": 1,
                    "compute_ms": 2,
                    "total_ms": 3,
                    "outcome": "success",
                    "result": result,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )

        self.assertEqual(response.status_code, 200, response.content)
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="bib_recognition")
        self.assertEqual(state.status, PhotoProcessingState.Status.SUCCEEDED)
        reading = BibReading.objects.get(photo=photo)
        self.assertEqual(reading.number, "007")
        self.assertEqual(reading.source_attempt_id, state.accepted_attempt_id)
