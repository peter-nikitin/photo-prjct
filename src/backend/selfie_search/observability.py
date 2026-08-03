"""Strict, privacy-bounded JSON events owned by the selfie-search service.

This module intentionally contains no generic logging integration.  Callers provide one of the
small event contracts below; invalid producer input is rejected before the logger is touched and
failures in the logger itself are contained so that observability cannot change product behavior.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

SCHEMA_VERSION = 1
SERVICE = "web"
MAX_BOUNDED_INTEGER = 2**31 - 1
_ENVIRONMENTS = frozenset({"local", "test", "staging", "production"})
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_INTEGER_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")
_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class SelfieEventContractError(ValueError):
    """Raised when an owned event payload does not satisfy its fixed contract."""


# A short alias keeps the local exception discoverable without introducing a shared framework.
ObservabilityContractError = SelfieEventContractError


class SelfieEventName(StrEnum):
    SUBMISSION_FINISHED = "selfie_submission_finished"
    RANKING_FINISHED = "selfie_ranking_finished"
    SEARCH_TERMINAL = "selfie_search_terminal"


_SUBMISSION_REASONS = frozenset(
    {
        "",
        "missing_or_empty",
        "unsupported_format",
        "corrupt_image",
        "source_too_large",
        "normalized_too_large",
        "pixel_limit_exceeded",
        "storage_unavailable",
    }
)
_ACTUAL_FORMATS = frozenset({"jpeg", "png", "heic", "heif", "unknown"})
_DECLARED_TYPES = frozenset({"jpeg", "png", "heic", "heif", "octet_stream", "missing", "other"})
_SOURCE_SIZE_BUCKETS = frozenset(
    {"empty", "le_1mib", "le_5mib", "le_10mib", "le_20mib", "gt_20mib"}
)
_RANKING_OUTCOMES = frozenset({"succeeded", "incompatible"})
_TERMINAL_STATUSES = frozenset(
    {"ready", "no_face", "multiple_faces", "quality_rejected", "search_unavailable", "failed"}
)
_TERMINAL_FAILURE_CODES = frozenset(
    {"", "no_face", "multiple_faces", "quality_rejected", "search_unavailable", "failed"}
)
_TERMINAL_FAILURE_BY_STATUS = {
    "ready": "",
    "no_face": "no_face",
    "multiple_faces": "multiple_faces",
    "quality_rejected": "quality_rejected",
    "search_unavailable": "",
    "failed": "failed",
}
OBSERVABILITY_FAILURE_MARKER = "selfie_observability_emit_failed"

_EVENT_FIELDS: dict[SelfieEventName, frozenset[str]] = {
    SelfieEventName.SUBMISSION_FINISHED: frozenset(
        {
            "event_id",
            "outcome",
            "reason_code",
            "search_id",
            "actual_format",
            "declared_type",
            "source_size_bucket",
            "duration_ms",
        }
    ),
    SelfieEventName.RANKING_FINISHED: frozenset(
        {
            "event_id",
            "search_id",
            "attempt_id",
            "outcome",
            "eligible_photo_count",
            "eligible_face_count",
            "matched_photo_count",
            "load_ms",
            "rank_ms",
            "configuration_hash",
        }
    ),
    SelfieEventName.SEARCH_TERMINAL: frozenset(
        {
            "event_id",
            "search_id",
            "status",
            "matched_photo_count",
            "attempt_count",
            "elapsed_ms",
            "failure_code",
            "cleanup_confirmed",
        }
    ),
}


def emit_selfie_event(
    logger: logging.Logger,
    *,
    event: SelfieEventName,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Validate and emit one compact JSON event, swallowing logger/serialization failures.

    The payload is fully normalized before ``logger.log`` is called.  In particular, no caller
    supplied value is interpolated into an ordinary log message and no unknown field can make it
    into the serialized object.
    """

    if not isinstance(level, int) or isinstance(level, bool):
        raise SelfieEventContractError("invalid log level")
    event_name = _event_name(event)
    payload = _validated_payload(event_name, fields)
    try:
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except Exception:
        _emit_failure_marker(logger)
        return
    try:
        logger.log(level, serialized)
    except Exception:
        _emit_failure_marker(logger)


def declared_type_label(value: object) -> str:
    """Return the bounded declared content-type label used by submission events."""

    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "other"
    normalized = value.strip().lower()
    if not normalized:
        return "missing"
    if normalized in {"jpeg", "jpg", "image/jpeg"}:
        return "jpeg"
    if normalized in {"png", "image/png"}:
        return "png"
    if normalized in {"heic", "image/heic"}:
        return "heic"
    if normalized in {"heif", "image/heif"}:
        return "heif"
    if normalized in {"octet-stream", "application/octet-stream"}:
        return "octet_stream"
    return "other"


def source_size_bucket(value: object) -> str:
    """Return the fixed source-size bucket without exposing a byte count."""

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return "empty"
    if value <= 1 * 1024 * 1024:
        return "le_1mib"
    if value <= 5 * 1024 * 1024:
        return "le_5mib"
    if value <= 10 * 1024 * 1024:
        return "le_10mib"
    if value <= 20 * 1024 * 1024:
        return "le_20mib"
    return "gt_20mib"


def _event_name(value: object) -> SelfieEventName:
    if isinstance(value, SelfieEventName):
        return value
    if not isinstance(value, str):
        raise SelfieEventContractError("unknown selfie event")
    try:
        return SelfieEventName(value)
    except (TypeError, ValueError) as error:
        raise SelfieEventContractError("unknown selfie event") from error


def _validated_payload(event: SelfieEventName, fields: dict[str, object]) -> dict[str, object]:
    expected = _EVENT_FIELDS[event]
    if set(fields) != expected:
        raise SelfieEventContractError("event fields do not match the contract")
    normalized: dict[str, object]
    if event is SelfieEventName.SUBMISSION_FINISHED:
        normalized = _submission_fields(fields)
    elif event is SelfieEventName.RANKING_FINISHED:
        normalized = _ranking_fields(fields)
    else:
        normalized = _terminal_fields(fields)
    return {
        "schema_version": SCHEMA_VERSION,
        "event": event.value,
        "occurred_at": _timestamp(),
        "service": SERVICE,
        "environment": _environment(),
        **normalized,
    }


def _submission_fields(fields: dict[str, object]) -> dict[str, object]:
    outcome = fields["outcome"]
    reason = fields["reason_code"]
    search_id = fields["search_id"]
    if outcome not in {"accepted", "rejected", "storage_unavailable"}:
        raise SelfieEventContractError("invalid submission outcome")
    if not isinstance(reason, str) or reason not in _SUBMISSION_REASONS:
        raise SelfieEventContractError("invalid submission reason")
    if outcome == "accepted" and (reason != "" or search_id is None):
        raise SelfieEventContractError("accepted submission has an invalid correlation")
    if outcome == "rejected" and (reason not in _SUBMISSION_REASONS - {"", "storage_unavailable"}):
        raise SelfieEventContractError("rejected submission has an invalid reason")
    if outcome == "storage_unavailable" and (
        reason != "storage_unavailable" or search_id is not None
    ):
        raise SelfieEventContractError("storage failure has an invalid correlation")
    if outcome == "rejected" and search_id is not None:
        raise SelfieEventContractError("rejected submission must not have a search id")
    return {
        "event_id": _opaque_id(fields["event_id"]),
        "outcome": outcome,
        "reason_code": reason,
        "search_id": _opaque_id(search_id, nullable=True, allow_integer=False),
        "actual_format": _enum(fields["actual_format"], _ACTUAL_FORMATS, "actual format"),
        "declared_type": _enum(fields["declared_type"], _DECLARED_TYPES, "declared type"),
        "source_size_bucket": _enum(
            fields["source_size_bucket"], _SOURCE_SIZE_BUCKETS, "source size bucket"
        ),
        "duration_ms": _bounded_int(fields["duration_ms"], nullable=False),
    }


def _worker_like_ids(fields: dict[str, object], names: tuple[str, ...]) -> dict[str, object]:
    return {name: _opaque_id(fields[name], allow_integer=name == "event_id") for name in names}


def _ranking_fields(fields: dict[str, object]) -> dict[str, object]:
    outcome = fields["outcome"]
    if outcome not in _RANKING_OUTCOMES:
        raise SelfieEventContractError("invalid ranking outcome")
    return {
        **_worker_like_ids(fields, ("event_id", "search_id", "attempt_id")),
        "outcome": outcome,
        "eligible_photo_count": _bounded_int(fields["eligible_photo_count"], nullable=False),
        "eligible_face_count": _bounded_int(fields["eligible_face_count"], nullable=False),
        "matched_photo_count": _bounded_int(fields["matched_photo_count"], nullable=False),
        "load_ms": _bounded_int(fields["load_ms"], nullable=True),
        "rank_ms": _bounded_int(fields["rank_ms"], nullable=True),
        "configuration_hash": _hash(fields["configuration_hash"]),
    }


def _terminal_fields(fields: dict[str, object]) -> dict[str, object]:
    status = fields["status"]
    if status not in _TERMINAL_STATUSES:
        raise SelfieEventContractError("invalid terminal status")
    failure_code = fields["failure_code"]
    if not isinstance(failure_code, str) or failure_code not in _TERMINAL_FAILURE_CODES:
        raise SelfieEventContractError("invalid terminal failure code")
    if failure_code != _TERMINAL_FAILURE_BY_STATUS[status]:
        raise SelfieEventContractError("terminal status and failure code do not match")
    if fields["cleanup_confirmed"] is not True:
        raise SelfieEventContractError("terminal event requires confirmed cleanup")
    return {
        **_worker_like_ids(fields, ("event_id", "search_id")),
        "status": status,
        "matched_photo_count": _bounded_int(fields["matched_photo_count"], nullable=False),
        "attempt_count": _bounded_int(fields["attempt_count"], nullable=False),
        "elapsed_ms": _bounded_int(fields["elapsed_ms"], nullable=False),
        "failure_code": failure_code,
        "cleanup_confirmed": True,
    }


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SelfieEventContractError(f"invalid {label}")
    return value


def _bounded_int(value: object, *, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_BOUNDED_INTEGER
    ):
        raise SelfieEventContractError("integer is outside the bounded contract")
    return value


def _opaque_id(value: object, *, nullable: bool = False, allow_integer: bool = True) -> str | None:
    if value is None and nullable:
        return None
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
    raise SelfieEventContractError("invalid opaque correlation id")


def _hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
        raise SelfieEventContractError("invalid configuration hash")
    return value.lower()


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
