from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from photo_worker.observability import (
    OBSERVABILITY_FAILURE_MARKER,
    SelfieWorkerEventContractError,
    emit_selfie_worker_event,
)


class _CaptureLogger(logging.Logger):
    def __init__(self) -> None:
        super().__init__("capture", logging.DEBUG)
        self.calls: list[tuple[int, str]] = []

    def log(self, level: int, message: str) -> None:  # type: ignore[override]
        self.calls.append((level, message))


def _fields(**overrides: object) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "event_id": 17,
        "search_id": uuid4(),
        "job_id": uuid4(),
        "attempt_id": uuid4(),
        "outcome": "succeeded",
        "reason_code": "",
        "retryable": False,
        "download_ms": 4,
        "compute_ms": 7,
        "total_ms": 11,
    }
    fields.update(overrides)
    return fields


_PRIVACY_SENTINELS = {
    "bearer": "Bearer SECRET-BEARER",
    "url": "https://storage.example/signed?token=SECRET-URL",
    "key": "selfie-search/SECRET-OBJECT-KEY",
    "filename": "SECRET-FILENAME.jpg",
    "ip": "203.0.113.44",
    "ua": "SECRET-USER-AGENT",
    "vector": "[0.1,0.2]",
    "photo": "SECRET-PHOTO-ID",
    "face": "SECRET-FACE-ID",
    "exception": "SECRET-EXCEPTION-TEXT",
}
_WORKER_PRIVACY_CASES = [(field, _fields()) for field in _fields()]


def test_worker_event_has_exact_compact_envelope_and_uuid_conversion() -> None:
    logger = _CaptureLogger()
    fields = _fields()

    emit_selfie_worker_event(logger, event="selfie_worker_attempt_finished", **fields)

    assert len(logger.calls) == 1
    level, line = logger.calls[0]
    assert level == logging.INFO
    assert "\n" not in line
    assert " " not in line
    payload = json.loads(line)
    assert set(payload) == {
        "schema_version",
        "event",
        "occurred_at",
        "service",
        "environment",
        *fields,
    }
    assert payload["schema_version"] == 1
    assert payload["event"] == "selfie_worker_attempt_finished"
    assert payload["service"] == "worker"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", payload["occurred_at"])
    datetime.strptime(payload["occurred_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert payload["event_id"] == "17"
    for key in ("search_id", "job_id", "attempt_id"):
        assert payload[key] == str(fields[key])


@pytest.mark.parametrize("outcome", ["failed"])
@pytest.mark.parametrize(
    "reason_code",
    [
        "decode_failed",
        "download_authorization_expired",
        "fingerprint_mismatch",
        "input_too_large",
        "model_inference_error",
        "model_inference_timeout",
        "network_interruption",
        "no_face_detected",
        "multiple_faces_detected",
        "quality_rejected",
        "storage_unavailable",
        "unsupported_input",
    ],
)
def test_worker_failure_event_accepts_only_bounded_error_codes(
    outcome: str, reason_code: str
) -> None:
    logger = _CaptureLogger()
    emit_selfie_worker_event(
        logger,
        event="selfie_worker_attempt_finished",
        **_fields(
            outcome=outcome,
            reason_code=reason_code,
            retryable=reason_code
            in {
                "download_authorization_expired",
                "model_inference_timeout",
                "network_interruption",
                "storage_unavailable",
            },
        ),
    )
    assert json.loads(logger.calls[0][1])["reason_code"] == reason_code


def test_worker_event_allows_nullable_durations() -> None:
    logger = _CaptureLogger()
    emit_selfie_worker_event(
        logger,
        event="selfie_worker_attempt_finished",
        **_fields(download_ms=None, compute_ms=None, total_ms=None),
    )
    payload = json.loads(logger.calls[0][1])
    assert payload["download_ms"] is None
    assert payload["compute_ms"] is None
    assert payload["total_ms"] is None


@pytest.mark.parametrize(
    "fields",
    [
        _fields(unexpected={"nested": "value"}),
        _fields(download_ms=-1),
        _fields(compute_ms=2**63),
        _fields(outcome="failed", reason_code=""),
        _fields(outcome="succeeded", reason_code="network_interruption"),
        _fields(outcome="succeeded", retryable=True),
        _fields(attempt_id="SECRET-ATTEMPT-ID"),
    ],
)
def test_worker_event_rejects_unknown_invalid_or_sensitive_values(
    fields: dict[str, Any],
) -> None:
    with pytest.raises(SelfieWorkerEventContractError):
        emit_selfie_worker_event(_CaptureLogger(), event="selfie_worker_attempt_finished", **fields)


@pytest.mark.parametrize("field,fields", _WORKER_PRIVACY_CASES)
def test_worker_privacy_matrix_rejects_every_sentinel_for_every_field(
    field: str, fields: dict[str, Any]
) -> None:
    logger = _CaptureLogger()
    for sentinel in _PRIVACY_SENTINELS.values():
        candidate = fields | {field: sentinel}
        with pytest.raises(SelfieWorkerEventContractError):
            emit_selfie_worker_event(
                logger,
                event="selfie_worker_attempt_finished",
                **candidate,
            )
        assert logger.calls == []


def test_worker_logger_and_serialization_failures_are_contained() -> None:
    class FailingLogger(logging.Logger):
        def __init__(self) -> None:
            super().__init__("failing", logging.DEBUG)

        def log(self, _level: int, _message: str) -> None:  # type: ignore[override]
            raise RuntimeError("logger unavailable")

    emit_selfie_worker_event(FailingLogger(), event="selfie_worker_attempt_finished", **_fields())

    class ExplodingValue:
        def __str__(self) -> str:
            raise RuntimeError("must never serialize")

    with pytest.raises(SelfieWorkerEventContractError):
        emit_selfie_worker_event(
            _CaptureLogger(),
            event="selfie_worker_attempt_finished",
            **_fields(reason_code=ExplodingValue()),
        )


def test_worker_serialization_failure_emits_fixed_marker_without_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_serialization(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("SECRET-EXCEPTION-TEXT")

    monkeypatch.setattr("photo_worker.observability.json.dumps", fail_serialization)
    logger = _CaptureLogger()

    emit_selfie_worker_event(logger, event="selfie_worker_attempt_finished", **_fields())

    assert logger.calls == [(logging.ERROR, OBSERVABILITY_FAILURE_MARKER)]
