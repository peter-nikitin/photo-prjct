from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import cast
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo

from processing import contracts
from processing.bib_validation import BibResultError, validate_bib_result
from processing.models import BibReading, PhotoProcessingState, ProcessingAttempt, ProcessingJob
from processing.services.bibs import bib_configuration, complete_bib_attempt
from processing.services.enrollment import request_bib_recognition
from processing.services.jobs import claim_job

SOURCE_SHA256 = "b" * 64
CONFIGURATION_SHA256 = "c" * 64


def candidate(
    number: str,
    *,
    visual_numbers: list[str] | None = None,
    visual_status: str = "complete",
    raw_text: str | None = None,
    raw_response: str | None = None,
) -> dict[str, object]:
    if visual_status == "uncertain":
        visual: dict[str, object] = {
            "status": "uncertain",
            "raw_response": raw_response or "malformed",
            "numbers": None,
            "error_code": "malformed_response",
            "inference_ms": 4.0,
        }
    else:
        visual = {
            "status": "complete",
            "raw_response": raw_response or json.dumps({"numbers": visual_numbers or []}),
            "numbers": visual_numbers or [],
            "error_code": None,
            "inference_ms": 4.0,
        }
    return {
        "candidate_id": hashlib.sha256(f"{SOURCE_SHA256}\0{number}".encode()).hexdigest()[:24],
        "number": number,
        "raw_text": raw_text if raw_text is not None else number,
        "confidence": 0.9,
        "polygon": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]],
        "supporting_region_count": 1,
        "crop": [0, 0, 384, 384],
        "visual": visual,
    }


def result(candidates: list[dict[str, object]]) -> dict[str, object]:
    return {
        "source_sha256": SOURCE_SHA256,
        "configuration_sha256": CONFIGURATION_SHA256,
        "width": 1000,
        "height": 800,
        "candidates": candidates,
        "tile_count": 1,
        "ocr_preparation_ms": 1.0,
        "ocr_inference_ms": 2.0,
    }


def validate(value: object):
    return validate_bib_result(
        value,
        expected_source_sha256=SOURCE_SHA256,
        expected_configuration_sha256=CONFIGURATION_SHA256,
    )


def test_bib_contract_and_configuration_are_generation_one_and_bounded() -> None:
    assert contracts.BIB_RECOGNITION_CONTRACT == contracts.ProcessorContract(
        processor_type="bib_recognition",
        contract_version=1,
        processor_version=1,
    )
    configuration = bib_configuration()
    bib = cast(dict[str, object], configuration["bib_recognition"])
    worker = cast(dict[str, object], configuration["worker"])
    assert bib["generation"] == 1
    assert bib["result_max_bytes"] == 120 * 1024
    assert worker["api_response_max_bytes"] == 128 * 1024
    assert worker["terminal_result_max_bytes"] == 128 * 1024
    assert worker["concurrency"] == 1


def test_validation_accepts_only_exact_ascii_digits_and_preserves_leading_zeroes() -> None:
    validated = validate(result([candidate("0012", visual_numbers=["0012", "12"])]))
    assert validated.decisions[0].status == "accepted"
    assert validated.decisions[0].number == "0012"


@pytest.mark.parametrize(
    ("raw_text", "number"),
    [
        ("+7 916 123-45-67", "916"),
        ("СТАРТ 2026", "2026"),
        ("SHIRT 55", "55"),
        ("ARM 8", "8"),
    ],
)
def test_validation_rejects_reviewed_false_positive_classes_without_exact_visual_match(
    raw_text: str, number: str
) -> None:
    validated = validate(
        result([candidate(number, raw_text=raw_text, visual_numbers=[number + "0"])])
    )
    assert validated.decisions[0].status == "rejected"
    assert validated.decisions[0].reason == "exact_visual_mismatch"


def test_validation_retains_uncertain_candidate_and_empty_success() -> None:
    uncertain = validate(result([candidate("12", visual_status="uncertain")]))
    assert uncertain.decisions[0].status == "uncertain"
    assert uncertain.decisions[0].reason == "visual_reading_failed"
    empty = validate(result([]))
    assert empty.decisions == ()


@pytest.mark.parametrize("number", ["", "1" * 17, "１２", "12a"])
def test_validation_rejects_non_contract_digit_strings(number: str) -> None:
    with pytest.raises(BibResultError, match="candidate number"):
        validate(result([candidate(number, visual_numbers=[number])]))


def test_validation_rejects_worker_product_decisions() -> None:
    with pytest.raises(BibResultError, match="fields"):
        validate(result([]) | {"accepted": True})


def test_validation_rejects_duplicate_candidate_numbers() -> None:
    with pytest.raises(BibResultError, match="duplicate candidate number"):
        validate(
            result(
                [
                    candidate("42", visual_numbers=["42"]),
                    candidate("42", visual_numbers=["42"]),
                ]
            )
        )


def test_validation_result_size_is_bounded_without_truncation() -> None:
    value = result([candidate("42", visual_numbers=["42"])])
    raw_response = value["candidates"][0]["visual"]  # type: ignore[index]
    assert isinstance(raw_response, dict)
    raw_response["raw_response"] = ""
    base_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    raw_response["raw_response"] = "x" * (120 * 1024 - base_size)
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) == 120 * 1024
    validate(value)
    raw_response["raw_response"] += "x"
    with pytest.raises(BibResultError, match="120 KiB"):
        validate(value)


@override_settings(PHOTO_PROCESSING_WORKER_TOKEN="worker-secret")
class BibCompletionTests(TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user(username="bib-completion-owner")
        self.event = Event.objects.create(
            name="Bib completion event",
            slug="bib-completion-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        self.photo = Photo.objects.create(
            id="bib-completion-photo",
            event=self.event,
            src="",
            uploaded_by=user,
            original_key="originals/bib-completion-photo",
            original_filename="bib.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1,
        )

    def claim(self):
        request_bib_recognition(self.photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="bib_recognition",
            processor_version=1,
            worker_build="bib-worker",
        )
        assert isinstance(claimed, contracts.ClaimedJob)
        return claimed

    def completion_body(self, claimed, raw_result: dict[str, object]) -> dict[str, object]:
        raw_result["configuration_sha256"] = claimed.attempt.configuration["bib_recognition"][
            "inference_configuration_sha256"
        ]
        now = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {
            "job_id": str(claimed.job.id),
            "attempt_id": str(claimed.attempt.id),
            "contract_version": 1,
            "processor_type": "bib_recognition",
            "processor_version": 1,
            "worker_build": "bib-worker",
            "started_at": now,
            "finished_at": now,
            "download_ms": 1,
            "compute_ms": 2,
            "total_ms": 3,
            "outcome": "success",
            "result": raw_result,
        }

    def post_completion(self, claimed, body: dict[str, object], *, padding: int = 0):
        encoded = json.dumps(body, separators=(",", ":")).encode() + b" " * padding
        return self.client.post(
            f"/internal/photo-processing/v1/attempts/{claimed.attempt.id}/complete",
            data=encoded,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )

    def failure_body(self, claimed, *, error_code: str, retryable: bool) -> dict[str, object]:
        now = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {
            "job_id": str(claimed.job.id),
            "attempt_id": str(claimed.attempt.id),
            "contract_version": 1,
            "processor_type": "bib_recognition",
            "processor_version": 1,
            "worker_build": "bib-worker",
            "started_at": now,
            "finished_at": now,
            "download_ms": 1,
            "compute_ms": 2,
            "total_ms": 3,
            "outcome": "failure",
            "error_code": error_code,
            "error_detail": "bounded diagnostic",
            "retryable": retryable,
        }

    def test_completion_atomically_replaces_projection_with_only_accepted_numbers(self) -> None:
        claimed = self.claim()
        old_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=claimed.job.run,
            job=claimed.job,
            photo=self.photo,
            contract_version=1,
            processor_type="bib_recognition",
            processor_version=1,
            configuration=claimed.job.configuration,
            input_fingerprint=claimed.job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            accepted=True,
            terminal_at=timezone.now(),
            result={"old": True},
        )
        BibReading.objects.create(
            photo=self.photo,
            source_attempt=old_attempt,
            number="007",
            evidence={"decision": "accepted"},
        )
        raw = result(
            [
                candidate("007", visual_numbers=["007"]),
                candidate("42", visual_numbers=["420"]),
                candidate("8", visual_status="uncertain"),
            ]
        )

        response = self.post_completion(claimed, self.completion_body(claimed, raw))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(BibReading.objects.values_list("number", flat=True)), ["007"])
        reading = BibReading.objects.get()
        self.assertEqual(reading.source_attempt_id, claimed.attempt.id)
        self.assertEqual(reading.evidence["decision"], "accepted")
        claimed.attempt.refresh_from_db()
        self.assertEqual(
            [item["status"] for item in claimed.attempt.result["validation"]["decisions"]],
            ["accepted", "rejected", "uncertain"],
        )
        self.assertGreaterEqual(claimed.attempt.result["validation"]["duration_ms"], 0)

    def test_projection_validation_failure_rolls_back_completion_and_old_membership(self) -> None:
        claimed = self.claim()
        old_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=claimed.job.run,
            job=claimed.job,
            photo=self.photo,
            contract_version=1,
            processor_type="bib_recognition",
            processor_version=1,
            configuration=claimed.job.configuration,
            input_fingerprint=claimed.job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            accepted=True,
            terminal_at=timezone.now(),
            result={"old": True},
        )
        BibReading.objects.create(
            photo=self.photo,
            source_attempt=old_attempt,
            number="99",
            evidence={"decision": "accepted"},
        )
        raw = result([candidate("42", visual_numbers=["42"])])
        raw["configuration_sha256"] = claimed.attempt.configuration["bib_recognition"][
            "inference_configuration_sha256"
        ]

        with (
            patch(
                "processing.services.bibs.BibReading.full_clean",
                side_effect=ValidationError({"evidence": "invalid replacement evidence"}),
            ),
            self.assertRaises(ValidationError),
        ):
            complete_bib_attempt(claimed.attempt.id, result=raw)

        claimed.attempt.refresh_from_db()
        state = PhotoProcessingState.objects.get(photo=self.photo, processor_type="bib_recognition")
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)
        self.assertEqual(state.status, PhotoProcessingState.Status.PROCESSING)
        self.assertEqual(
            list(BibReading.objects.filter(photo=self.photo).values_list("number", flat=True)),
            ["99"],
        )

    def test_empty_success_clears_projection_and_duplicate_is_idempotent(self) -> None:
        claimed = self.claim()
        body = self.completion_body(claimed, result([]))

        first = self.post_completion(claimed, body)
        duplicate = self.post_completion(claimed, body)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        self.assertTrue(duplicate.json()["idempotent"])
        self.assertFalse(BibReading.objects.filter(photo=self.photo).exists())

    def test_duplicate_with_a_different_result_conflicts_and_preserves_projection(self) -> None:
        claimed = self.claim()
        body = self.completion_body(claimed, result([]))
        first = self.post_completion(claimed, body)
        different = body | {
            "result": result([candidate("42", visual_numbers=["42"])])
            | {
                "configuration_sha256": claimed.attempt.configuration["bib_recognition"][
                    "inference_configuration_sha256"
                ]
            }
        }

        duplicate = self.post_completion(claimed, different)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(duplicate.status_code, 409, duplicate.content)
        self.assertEqual(duplicate.json()["error"]["code"], "completion_conflict")
        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.result["recognition"]["candidates"], [])
        self.assertFalse(BibReading.objects.filter(photo=self.photo).exists())

    def test_exact_result_and_envelope_limits_accept_then_reject_plus_one(self) -> None:
        accepted = self.claim()
        raw = result([candidate("42", visual_numbers=["42"])])
        visual = raw["candidates"][0]["visual"]  # type: ignore[index]
        assert isinstance(visual, dict)
        visual["raw_response"] = ""
        raw_base = len(json.dumps(raw, separators=(",", ":")).encode())
        visual["raw_response"] = "x" * (120 * 1024 - raw_base)
        self.assertEqual(len(json.dumps(raw, separators=(",", ":")).encode()), 120 * 1024)
        body = self.completion_body(accepted, raw)
        body_size = len(json.dumps(body, separators=(",", ":")).encode())
        self.assertLess(body_size, 128 * 1024)

        response = self.post_completion(accepted, body, padding=128 * 1024 - body_size)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(BibReading.objects.values_list("number", flat=True)), ["42"])

        rejected_photo = Photo.objects.create(
            id="bib-over-limit-photo",
            event=self.event,
            src="",
            uploaded_by=self.photo.uploaded_by,
            original_key="originals/bib-over-limit-photo",
            original_filename="over.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1,
        )
        self.photo = rejected_photo
        rejected = self.claim()
        rejected_body = self.completion_body(rejected, result([]))
        rejected_size = len(json.dumps(rejected_body, separators=(",", ":")).encode())
        over = self.post_completion(
            rejected,
            rejected_body,
            padding=128 * 1024 - rejected_size + 1,
        )

        self.assertEqual(over.status_code, 400)
        rejected.attempt.refresh_from_db()
        self.assertEqual(rejected.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)
        self.assertFalse(BibReading.objects.filter(photo=rejected_photo).exists())

    def test_result_one_byte_over_limit_is_rejected_without_partial_projection(self) -> None:
        claimed = self.claim()
        raw = result([candidate("42", visual_numbers=["42"])])
        visual = raw["candidates"][0]["visual"]  # type: ignore[index]
        assert isinstance(visual, dict)
        visual["raw_response"] = ""
        raw_base = len(json.dumps(raw, separators=(",", ":")).encode())
        visual["raw_response"] = "x" * (120 * 1024 - raw_base + 1)

        response = self.post_completion(claimed, self.completion_body(claimed, raw))

        self.assertEqual(response.status_code, 400)
        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)
        state = PhotoProcessingState.objects.get(photo=self.photo, processor_type="bib_recognition")
        self.assertEqual(state.status, PhotoProcessingState.Status.PROCESSING)
        self.assertFalse(BibReading.objects.filter(photo=self.photo).exists())

    def test_failure_retryability_is_code_owned_and_terminal_failure_is_retained(self) -> None:
        retryable = self.claim()
        wrong = self.client.post(
            f"/internal/photo-processing/v1/attempts/{retryable.attempt.id}/fail",
            data=json.dumps(
                self.failure_body(retryable, error_code="model_inference_timeout", retryable=False)
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )
        self.assertEqual(wrong.status_code, 400)
        response = self.client.post(
            f"/internal/photo-processing/v1/attempts/{retryable.attempt.id}/fail",
            data=json.dumps(
                self.failure_body(retryable, error_code="model_inference_timeout", retryable=True)
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )
        self.assertEqual(response.status_code, 200, response.content)
        retryable.job.refresh_from_db()
        retryable.attempt.refresh_from_db()
        self.assertEqual(retryable.job.status, ProcessingJob.Status.RETRY_WAIT)
        self.assertEqual(retryable.attempt.error_code, "model_inference_timeout")

        terminal_photo = Photo.objects.create(
            id="bib-terminal-photo",
            event=self.event,
            src="",
            uploaded_by=self.photo.uploaded_by,
            original_key="originals/bib-terminal-photo",
            original_filename="terminal.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1,
        )
        self.photo = terminal_photo
        terminal = self.claim()
        failed = self.client.post(
            f"/internal/photo-processing/v1/attempts/{terminal.attempt.id}/fail",
            data=json.dumps(self.failure_body(terminal, error_code="ocr_failed", retryable=False)),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )
        self.assertEqual(failed.status_code, 200, failed.content)
        terminal.job.refresh_from_db()
        terminal.attempt.refresh_from_db()
        self.assertEqual(terminal.job.status, ProcessingJob.Status.FAILED)
        self.assertEqual(terminal.attempt.error_code, "ocr_failed")
        terminal_photo.refresh_from_db()
        self.assertEqual(
            terminal_photo.gallery_media_policy,
            Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
        )

    def test_failure_envelope_one_byte_over_limit_does_not_finish_attempt(self) -> None:
        claimed = self.claim()
        body = self.failure_body(claimed, error_code="ocr_failed", retryable=False)
        encoded = json.dumps(body, separators=(",", ":")).encode()

        response = self.client.post(
            f"/internal/photo-processing/v1/attempts/{claimed.attempt.id}/fail",
            data=encoded + b" " * (128 * 1024 - len(encoded) + 1),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer worker-secret",
        )

        self.assertEqual(response.status_code, 400, response.content)
        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.IN_PROGRESS)

    def test_expired_completion_is_retained_as_stale_without_projection(self) -> None:
        claimed = self.claim()
        ProcessingAttempt.objects.filter(pk=claimed.attempt.id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.post_completion(
            claimed,
            self.completion_body(claimed, result([candidate("42", visual_numbers=["42"])])),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["stale"])
        claimed.attempt.refresh_from_db()
        self.assertEqual(claimed.attempt.status, ProcessingAttempt.Status.EXPIRED)
        self.assertFalse(BibReading.objects.filter(photo=self.photo).exists())
