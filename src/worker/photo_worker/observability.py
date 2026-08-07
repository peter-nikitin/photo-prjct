"""Strict, privacy-bounded JSON events owned by the standalone selfie worker."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

SCHEMA_VERSION = 1
SERVICE = "worker"
MAX_BOUNDED_INTEGER = 2**31 - 1
_ENVIRONMENTS = frozenset({"local", "test", "staging", "production"})
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_INTEGER_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")


class SelfieWorkerEventContractError(ValueError):
    """Raised when a worker event payload does not satisfy its fixed contract."""


ObservabilityContractError = SelfieWorkerEventContractError


class SelfieWorkerEventName(StrEnum):
    ATTEMPT_FINISHED = "selfie_worker_attempt_finished"


_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "search_id",
        "job_id",
        "attempt_id",
        "outcome",
        "reason_code",
        "retryable",
        "download_ms",
        "compute_ms",
        "total_ms",
    }
)
_ERROR_CODES = frozenset(
    {
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
    }
)
_RETRYABLE_CODES = frozenset(
    {
        "download_authorization_expired",
        "fingerprint_mismatch",
        "model_inference_timeout",
        "network_interruption",
        "storage_unavailable",
    }
)
OBSERVABILITY_FAILURE_MARKER = "selfie_observability_emit_failed"


def emit_selfie_worker_event(
    logger: logging.Logger,
    *,
    event: str,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Validate and emit one compact worker JSON event without affecting worker control flow."""

    if not isinstance(level, int) or isinstance(level, bool):
        raise SelfieWorkerEventContractError("invalid log level")
    _event_name(event)
    payload = _validated_payload(fields)
    try:
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except Exception:
        _emit_failure_marker(logger)
        return
    try:
        logger.log(level, serialized)
    except Exception:
        _emit_failure_marker(logger)


def _event_name(value: object) -> SelfieWorkerEventName:
    if isinstance(value, SelfieWorkerEventName):
        return value
    if not isinstance(value, str):
        raise SelfieWorkerEventContractError("unknown worker event")
    try:
        return SelfieWorkerEventName(value)
    except (TypeError, ValueError) as error:
        raise SelfieWorkerEventContractError("unknown worker event") from error


def _validated_payload(fields: dict[str, object]) -> dict[str, object]:
    if set(fields) != _EVENT_FIELDS:
        raise SelfieWorkerEventContractError("event fields do not match the contract")
    outcome = fields["outcome"]
    reason_code = fields["reason_code"]
    retryable = fields["retryable"]
    if outcome not in {"succeeded", "failed"}:
        raise SelfieWorkerEventContractError("invalid worker outcome")
    if not isinstance(reason_code, str):
        raise SelfieWorkerEventContractError("invalid worker reason code")
    if outcome == "succeeded" and reason_code != "":
        raise SelfieWorkerEventContractError("successful worker event has a reason code")
    if outcome == "failed" and reason_code not in _ERROR_CODES:
        raise SelfieWorkerEventContractError("failed worker event has an invalid reason code")
    if not isinstance(retryable, bool):
        raise SelfieWorkerEventContractError("retryable must be boolean")
    if outcome == "succeeded" and retryable:
        raise SelfieWorkerEventContractError("successful worker event cannot be retryable")
    if outcome == "failed" and retryable != (reason_code in _RETRYABLE_CODES):
        raise SelfieWorkerEventContractError("retryable does not match the worker error code")
    return {
        "schema_version": SCHEMA_VERSION,
        "event": SelfieWorkerEventName.ATTEMPT_FINISHED.value,
        "occurred_at": _timestamp(),
        "service": SERVICE,
        "environment": _environment(),
        "event_id": _opaque_id(fields["event_id"]),
        "search_id": _opaque_id(fields["search_id"], allow_integer=False),
        "job_id": _opaque_id(fields["job_id"], allow_integer=False),
        "attempt_id": _opaque_id(fields["attempt_id"], allow_integer=False),
        "outcome": outcome,
        "reason_code": reason_code,
        "retryable": retryable,
        "download_ms": _bounded_int(fields["download_ms"]),
        "compute_ms": _bounded_int(fields["compute_ms"]),
        "total_ms": _bounded_int(fields["total_ms"]),
    }


def _bounded_int(value: object) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_BOUNDED_INTEGER
    ):
        raise SelfieWorkerEventContractError("duration is outside the bounded contract")
    return value


def _opaque_id(value: object, *, allow_integer: bool = True) -> str:
    if isinstance(value, UUID):
        return str(value)
    if (
        allow_integer
        and isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_BOUNDED_INTEGER
    ):
        return str(value)
    if isinstance(value, str):
        if _UUID_PATTERN.fullmatch(value):
            return str(UUID(value))
        if allow_integer and _INTEGER_PATTERN.fullmatch(value):
            return str(int(value))
    raise SelfieWorkerEventContractError("invalid opaque correlation id")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _environment() -> str:
    value = os.environ.get("DEPLOYMENT_TARGET", "local").strip().lower()
    return value if value in _ENVIRONMENTS else "local"


def _emit_failure_marker(logger: logging.Logger) -> None:
    try:
        logger.log(logging.ERROR, OBSERVABILITY_FAILURE_MARKER)
    except Exception:
        return
