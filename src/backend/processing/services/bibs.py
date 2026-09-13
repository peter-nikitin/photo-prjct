from __future__ import annotations

import json
from copy import deepcopy
from time import perf_counter
from typing import Any, cast
from uuid import UUID

from django.db import transaction

from processing.bib_validation import (
    MAX_STORED_RESULT_BYTES,
    BibResultError,
    ValidatedBibResult,
    validate_bib_result,
)
from processing.contracts import AttemptCompletion
from processing.models import (
    BibReading,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingLateReceipt,
)
from processing.services import jobs

BIB_INFERENCE_CONFIGURATION_SHA256 = (
    "32b3f2c94202df7c90e5c799c9ca21b760d5d2ac9c376fe578f6f330e415edf9"
)

_BIB_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 100,
    "report_max_bytes": 262_144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "bib_recognition": {
        "generation": 1,
        "inference_configuration_sha256": BIB_INFERENCE_CONFIGURATION_SHA256,
        "deadline_seconds": 300,
        "result_max_bytes": 120 * 1024,
    },
    "worker": {
        "api_response_max_bytes": 128 * 1024,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": 100_000_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 128 * 1024,
    },
}


def bib_configuration() -> dict[str, object]:
    """Return the complete immutable bib-recognition generation-one configuration."""
    return deepcopy(_BIB_CONFIGURATION)


def complete_bib_attempt(
    attempt_id: UUID,
    *,
    result: dict[str, Any],
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
) -> AttemptCompletion:
    """Validate, persist, and project one current bib completion atomically."""
    identity = ProcessingAttempt.objects.only("configuration", "input_fingerprint").get(
        pk=attempt_id
    )
    bib = identity.configuration.get("bib_recognition")
    if not isinstance(bib, dict):
        raise BibResultError("bib attempt has an invalid configuration")
    source_sha256 = result.get("source_sha256")
    expected_source_sha256 = identity.input_fingerprint.get("source_sha256", source_sha256)
    expected_configuration_sha256 = bib.get("inference_configuration_sha256")
    if not isinstance(expected_source_sha256, str) or not isinstance(
        expected_configuration_sha256, str
    ):
        raise BibResultError("bib attempt is missing validation identities")
    validation_started = perf_counter()
    validated = validate_bib_result(
        result,
        expected_source_sha256=expected_source_sha256,
        expected_configuration_sha256=expected_configuration_sha256,
    )
    validation_duration_ms = round((perf_counter() - validation_started) * 1000, 3)
    with transaction.atomic():
        _, _, _, _, _, locked_attempt = jobs._locked_context(attempt_id)
        stored_result = validated.as_stored_result()
        stored_validation = cast(dict[str, object], stored_result["validation"])
        stored_validation["duration_ms"] = validation_duration_ms
        existing_result = _terminal_result(locked_attempt)
        if existing_result is not None:
            _reuse_validation_duration(stored_result, existing_result)
        if (
            len(json.dumps(stored_result, ensure_ascii=False, separators=(",", ":")).encode())
            > MAX_STORED_RESULT_BYTES
        ):
            raise BibResultError("canonical bib result exceeds 128 KiB")
        completion = jobs.complete_attempt(
            attempt_id,
            result=stored_result,
            download_duration_ms=download_duration_ms,
            compute_duration_ms=compute_duration_ms,
            total_duration_ms=total_duration_ms,
            worker_started_at=worker_started_at,
            worker_finished_at=worker_finished_at,
        )
        if not completion.idempotent and not completion.stale:
            _replace_current_projection(completion.attempt, validated)
        return completion


def _terminal_result(attempt: ProcessingAttempt) -> dict[str, Any] | None:
    if attempt.status == ProcessingAttempt.Status.IN_PROGRESS:
        return None
    if attempt.status != ProcessingAttempt.Status.EXPIRED:
        return attempt.result
    late_payload = (
        ProcessingLateReceipt.objects.select_for_update()
        .filter(attempt=attempt)
        .values_list("payload", flat=True)
        .first()
    )
    result = late_payload.get("result") if isinstance(late_payload, dict) else None
    return result if isinstance(result, dict) else None


def _reuse_validation_duration(
    stored_result: dict[str, Any], existing_result: dict[str, Any]
) -> None:
    stored_validation = cast(dict[str, object], stored_result["validation"])
    existing_validation = existing_result.get("validation")
    if not isinstance(existing_validation, dict) or "duration_ms" not in existing_validation:
        stored_validation.pop("duration_ms", None)
        return
    stored_validation["duration_ms"] = existing_validation["duration_ms"]


def _replace_current_projection(attempt: ProcessingAttempt, validated: ValidatedBibResult) -> None:
    state = PhotoProcessingState.objects.select_for_update().get(
        photo_id=attempt.photo_id,
        processor_type=attempt.processor_type,
    )
    if not (
        state.status == PhotoProcessingState.Status.SUCCEEDED
        and state.current_attempt_id == attempt.id
        and state.accepted_attempt_id == attempt.id
        and attempt.status == ProcessingAttempt.Status.SUCCEEDED
        and attempt.accepted
    ):
        raise ValueError("bib projection requires the current accepted successful attempt")
    candidates = {
        cast(str, candidate["candidate_id"]): candidate
        for candidate in cast(list[dict[str, object]], validated.result["candidates"])
    }
    BibReading.objects.filter(photo_id=attempt.photo_id).delete()
    readings: list[BibReading] = []
    for decision in validated.decisions:
        if decision.status != "accepted":
            continue
        candidate = candidates[decision.candidate_id]
        reading = BibReading(
            photo_id=attempt.photo_id,
            source_attempt=attempt,
            number=decision.number,
            evidence={
                "decision": decision.status,
                "reason": decision.reason,
                "candidate_id": decision.candidate_id,
                "source_sha256": validated.result["source_sha256"],
                "configuration_sha256": validated.result["configuration_sha256"],
                "ocr_confidence": candidate["confidence"],
                "polygon": candidate["polygon"],
                "crop": candidate["crop"],
            },
        )
        reading.full_clean()
        readings.append(reading)
    BibReading.objects.bulk_create(readings)
