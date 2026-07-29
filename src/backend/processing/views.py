"""Strict JSON endpoints at the private worker trust boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from math import floor
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from ingestion.storage import ObjectMissing, StorageUnavailable

from processing.auth import has_worker_token
from processing.contracts import ClaimedJob, CompletionConflict, EmptyClaim
from processing.models import ProcessingAttempt, ProcessingConflictAudit
from processing.services.jobs import (
    complete_attempt,
    fail_attempt,
    heartbeat_attempt,
    recover_expired_attempts,
)
from processing.services.jobs import (
    refresh_download as refresh_download_attempt,
)
from processing.storage import ExactObjectDownloadStorage

_WARNING_CODE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_WORKER_BUILD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_NORMALIZED_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_SECRET_MARKER = re.compile(r"(?:[a-z][a-z0-9+.-]*://|x-amz-|signature=|credential=|token=)", re.I)
_SOURCE_FIELDS = {"DateTime", "DateTimeDigitized", "DateTimeOriginal"}
_EXIF_SOURCE_VALUE = re.compile(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")
_WARNING_CODES = {"capture_time_conflicting", "capture_time_malformed", "capture_time_missing"}
_FAILURES = {
    "decode_failed": (False, "The image could not be decoded."),
    "download_authorization_expired": (True, "Download authorization expired."),
    "fingerprint_mismatch": (False, "The downloaded input did not match its fingerprint."),
    "input_too_large": (False, "The input exceeded its declared limit."),
    "network_interruption": (True, "A temporary network interruption occurred."),
    "storage_unavailable": (True, "Object storage is temporarily unavailable."),
    "unsupported_input": (False, "The input is unsupported."),
}


class FingerprintInvariant(Exception):
    pass


class LeaseExpired(Exception):
    pass


def _endpoint(view: Callable[..., JsonResponse]) -> Callable[..., HttpResponse]:
    @csrf_exempt
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if request.method != "POST":
            response = _error("method_not_allowed", "Only POST is allowed.", status=405)
            response["Allow"] = "POST"
            return response
        if not has_worker_token(request):
            return _error("worker_unauthorized", "Unauthorized.", status=401)
        return view(request, *args, **kwargs)

    return wrapped


@_endpoint
def claim(request: HttpRequest) -> JsonResponse:
    data, error = _json_object(
        request,
        required={
            "contract_version",
            "processor_type",
            "processor_version",
            "worker_build",
            "lease_seconds",
        },
    )
    if error is not None:
        return error
    assert data is not None
    if not (
        _positive_int(data["contract_version"])
        and _bounded_string(data["processor_type"], maximum=64)
        and _positive_int(data["processor_version"])
        and _safe_worker_build(data["worker_build"])
        and _positive_int(data["lease_seconds"])
    ):
        return _invalid_request()
    recover_expired_attempts()
    try:
        payload = _claim_with_grant(data)
    except ValueError:
        return _invalid_request()
    except FingerprintInvariant:
        return _error("input_invariant", "The queued input is invalid.", status=500)
    except LeaseExpired:
        return _lease_not_current()
    except (StorageUnavailable, ObjectMissing):
        return _error(
            "storage_unavailable", "Object storage is temporarily unavailable.", status=503
        )
    return JsonResponse(payload)


@_endpoint
def heartbeat(request: HttpRequest, attempt_id: str) -> JsonResponse:
    parsed_attempt_id, error = _attempt_id(attempt_id)
    if error is not None:
        return error
    assert parsed_attempt_id is not None
    data, error = _json_object(request, required={"lease_seconds"})
    if error is not None:
        return error
    assert data is not None
    if not _positive_int(data["lease_seconds"]):
        return _invalid_request()
    try:
        attempt = heartbeat_attempt(parsed_attempt_id, lease_seconds=data["lease_seconds"])
    except ProcessingAttempt.DoesNotExist:
        return _not_found()
    except ValueError:
        return _invalid_request()
    if attempt is None:
        return _lease_not_current()
    return JsonResponse({"attempt": _attempt_payload(attempt)})


@_endpoint
def refresh_download(request: HttpRequest, attempt_id: str) -> JsonResponse:
    parsed_attempt_id, error = _attempt_id(attempt_id)
    if error is not None:
        return error
    assert parsed_attempt_id is not None
    _, error = _json_object(request, required=set())
    if error is not None:
        return error
    try:
        payload = _refresh_with_grant(parsed_attempt_id)
    except ProcessingAttempt.DoesNotExist:
        return _not_found()
    except LeaseExpired:
        return _lease_not_current()
    except FingerprintInvariant:
        return _error("input_invariant", "The queued input is invalid.", status=500)
    except (StorageUnavailable, ObjectMissing):
        return _error(
            "storage_unavailable", "Object storage is temporarily unavailable.", status=503
        )
    if payload is None:
        return _lease_not_current()
    return JsonResponse(payload)


@_endpoint
def complete(request: HttpRequest, attempt_id: str) -> JsonResponse:
    parsed_attempt_id, error = _attempt_id(attempt_id)
    if error is not None:
        return error
    assert parsed_attempt_id is not None
    if not ProcessingAttempt.objects.filter(pk=parsed_attempt_id).exists():
        return _not_found()
    data, error = _json_object(request, required=_success_fields())
    if error is not None:
        return error
    assert data is not None
    if not _valid_envelope(data, parsed_attempt_id, outcome="success"):
        return _invalid_result()
    try:
        completion = complete_attempt(
            parsed_attempt_id,
            result=data["result"],
            download_duration_ms=data["download_ms"],
            compute_duration_ms=data["compute_ms"],
            total_duration_ms=data["total_ms"],
            worker_started_at=data["started_at"],
            worker_finished_at=data["finished_at"],
        )
    except ProcessingAttempt.DoesNotExist:
        return _not_found()
    except CompletionConflict as conflict:
        _audit_conflict(conflict)
        return _completion_conflict()
    return JsonResponse(
        _completion_payload(completion.attempt, completion.idempotent, completion.stale)
    )


@_endpoint
def fail(request: HttpRequest, attempt_id: str) -> JsonResponse:
    parsed_attempt_id, error = _attempt_id(attempt_id)
    if error is not None:
        return error
    assert parsed_attempt_id is not None
    if not ProcessingAttempt.objects.filter(pk=parsed_attempt_id).exists():
        return _not_found()
    data, error = _json_object(request, required=_failure_fields())
    if error is not None:
        return error
    assert data is not None
    if not _valid_envelope(data, parsed_attempt_id, outcome="failure"):
        return _invalid_result()
    try:
        completion = fail_attempt(
            parsed_attempt_id,
            error_code=data["error_code"],
            error_detail=_FAILURES[data["error_code"]][1],
            canonical_error_detail=data["error_detail"],
            retryable=_FAILURES[data["error_code"]][0],
            download_duration_ms=data["download_ms"],
            compute_duration_ms=data["compute_ms"],
            total_duration_ms=data["total_ms"],
            worker_started_at=data["started_at"],
            worker_finished_at=data["finished_at"],
        )
    except ProcessingAttempt.DoesNotExist:
        return _not_found()
    except CompletionConflict as conflict:
        _audit_conflict(conflict)
        return _completion_conflict()
    return JsonResponse(
        _completion_payload(completion.attempt, completion.idempotent, completion.stale)
    )


def _claim(data: dict[str, Any]) -> ClaimedJob | EmptyClaim:
    from processing.services.jobs import claim_job

    return claim_job(
        contract_version=data["contract_version"],
        processor_type=data["processor_type"],
        processor_version=data["processor_version"],
        worker_build=data["worker_build"],
        lease_seconds=data["lease_seconds"],
    )


def _claim_with_grant(data: dict[str, Any]) -> dict[str, object]:
    """Keep the claim rows locked until local signature construction succeeds or rolls back."""
    with transaction.atomic():
        claimed = _claim(data)
        if isinstance(claimed, EmptyClaim):
            return {"empty": True, "suggested_delay_seconds": claimed.suggested_delay_seconds}
        fingerprint = _input_fingerprint(claimed.job.input_fingerprint)
        grant = ExactObjectDownloadStorage().create_download_grant(
            final_key=cast(str, fingerprint["original_key"]),
            max_ttl_seconds=_remaining_lease(claimed.attempt),
        )
        _validate_grant_lease(claimed.attempt, grant.expires_at)
        return _claimed_payload(claimed, grant.url, grant.expires_at, fingerprint)


def _refresh_with_grant(attempt_id: UUID) -> dict[str, object] | None:
    """Revalidate and sign under the same outer transaction so recovery cannot interleave."""
    with transaction.atomic():
        attempt = refresh_download_attempt(attempt_id)
        if attempt is None:
            return None
        fingerprint = _input_fingerprint(attempt.input_fingerprint)
        grant = ExactObjectDownloadStorage().create_download_grant(
            final_key=cast(str, fingerprint["original_key"]),
            max_ttl_seconds=_remaining_lease(attempt),
        )
        _validate_grant_lease(attempt, grant.expires_at)
        return {
            "attempt": _attempt_payload(attempt),
            "download_url": grant.url,
            "download_expires_at": grant.expires_at.isoformat(),
        }


def _input_fingerprint(value: object) -> dict[str, int | str | None]:
    if not isinstance(value, dict):
        raise FingerprintInvariant()
    key = value.get("original_key")
    size = value.get("original_size")
    content_type = value.get("original_content_type")
    etag = value.get("verified_source_etag")
    if (
        not isinstance(key, str)
        or not re.fullmatch(r"originals/[0-9a-f]{32}", key)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or content_type != "image/jpeg"
        or (etag is not None and not isinstance(etag, str))
    ):
        raise FingerprintInvariant()
    return {
        "original_key": key,
        "original_size": size,
        "original_content_type": content_type,
        "verified_source_etag": etag,
    }


def _remaining_lease(attempt: ProcessingAttempt) -> int:
    if attempt.lease_expires_at is None:
        raise LeaseExpired()
    # Reserve one whole second for the separate storage clock/read after this calculation.
    seconds = floor((attempt.lease_expires_at - timezone.now()).total_seconds()) - 1
    if seconds < 1:
        raise LeaseExpired()
    return seconds


def _validate_grant_lease(attempt: ProcessingAttempt, expires_at: datetime) -> None:
    if (
        attempt.lease_expires_at is None
        or timezone.now() >= attempt.lease_expires_at
        or expires_at > attempt.lease_expires_at
    ):
        raise LeaseExpired()


def _json_object(
    request: HttpRequest, *, required: set[str]
) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    declared = request.headers.get("Content-Length")
    if declared is not None and (
        not declared.isdecimal() or int(declared) > settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES
    ):
        return None, _invalid_request()
    try:
        raw = request.read(settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES + 1)
        if len(raw) > settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES:
            return None, _invalid_request()
        parsed = json.loads(raw or b"{}")
    except (RequestDataTooBig, json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return None, _invalid_request()
    if request.content_type != "application/json":
        return None, _invalid_request()
    if not isinstance(parsed, dict) or set(parsed) != required:
        return None, _invalid_request()
    return parsed, None


def _claimed_payload(
    claimed: ClaimedJob, url: str, expires_at: datetime, fingerprint: dict[str, int | str | None]
) -> dict[str, object]:
    job = claimed.job
    attempt = claimed.attempt
    return {
        "empty": False,
        "job": {
            "id": str(job.id),
            "attempt_id": str(attempt.id),
            "contract_version": job.contract_version,
            "processor_type": job.processor_type,
            "processor_version": job.processor_version,
            "configuration": job.configuration,
            "photo_id": job.photo_id,
            "event_id": str(job.event_id),
            "run_id": str(job.run_id),
            "input_fingerprint": job.input_fingerprint,
            "input_limits": {
                "max_bytes": fingerprint["original_size"],
                "content_type": fingerprint["original_content_type"],
            },
            "lease_expires_at": attempt.lease_expires_at.isoformat(),
            "download_url": url,
            "download_expires_at": expires_at.isoformat(),
        },
    }


def _attempt_payload(attempt: ProcessingAttempt) -> dict[str, object]:
    return {
        "id": str(attempt.id),
        "status": attempt.status,
        "lease_expires_at": attempt.lease_expires_at.isoformat()
        if attempt.lease_expires_at is not None
        else None,
    }


def _completion_payload(
    attempt: ProcessingAttempt, idempotent: bool, stale: bool
) -> dict[str, object]:
    return {"attempt": _attempt_payload(attempt), "idempotent": idempotent, "stale": stale}


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _duration(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 86_400_000


def _bounded_string(value: object, *, maximum: int) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= maximum and "\x00" not in value


def _success_fields() -> set[str]:
    return _envelope_fields() | {"result"}


def _failure_fields() -> set[str]:
    return _envelope_fields() | {"error_code", "retryable", "error_detail"}


def _envelope_fields() -> set[str]:
    return {
        "job_id",
        "attempt_id",
        "contract_version",
        "processor_type",
        "processor_version",
        "worker_build",
        "started_at",
        "finished_at",
        "download_ms",
        "compute_ms",
        "total_ms",
        "outcome",
    }


def _attempt_id(value: str) -> tuple[UUID | None, JsonResponse | None]:
    try:
        return UUID(value), None
    except (TypeError, ValueError):
        return None, _error("invalid_attempt_id", "The attempt identifier is invalid.", status=404)


def _valid_envelope(data: dict[str, Any], attempt_id: UUID, *, outcome: str) -> bool:
    try:
        attempt = ProcessingAttempt.objects.select_related("job").get(pk=attempt_id)
    except ProcessingAttempt.DoesNotExist:
        return False
    started_at = _parse_timestamp(data["started_at"])
    finished_at = _parse_timestamp(data["finished_at"])
    if not (
        data["outcome"] == outcome
        and data["attempt_id"] == str(attempt.id)
        and data["job_id"] == str(attempt.job_id)
        and type(data["contract_version"]) is int
        and data["contract_version"] == attempt.contract_version == 1
        and data["processor_type"] == attempt.processor_type == "capture_metadata"
        and type(data["processor_version"]) is int
        and data["processor_version"] == attempt.processor_version == 1
        and _safe_worker_build(data["worker_build"])
        and data["worker_build"] == attempt.worker_build
        and started_at is not None
        and finished_at is not None
        and started_at <= finished_at
        and all(_duration(data[name]) for name in ("download_ms", "compute_ms", "total_ms"))
        and data["total_ms"] >= data["download_ms"] + data["compute_ms"]
    ):
        return False
    if outcome == "success":
        return _valid_result(data["result"])
    return (
        data["error_code"] in _FAILURES
        and type(data["retryable"]) is bool
        and data["retryable"] == _FAILURES[data["error_code"]][0]
        and isinstance(data["error_detail"], str)
        and len(data["error_detail"]) <= 512
        and _safe_error_detail(data["error_detail"])
    )


def _valid_result(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "capture_time",
        "source_field",
        "timezone_state",
        "source_value",
        "warnings",
    }:
        return False
    capture_time = value["capture_time"]
    if capture_time is not None and _parse_timestamp(capture_time) is None:
        return False
    if value["source_field"] is not None and value["source_field"] not in _SOURCE_FIELDS:
        return False
    if value["timezone_state"] not in {"explicit", "inferred_none", "not_applicable"}:
        return False
    if value["source_value"] is not None and not _safe_source_value(value["source_value"]):
        return False
    warnings = value["warnings"]
    valid_warnings = (
        isinstance(warnings, list)
        and len(warnings) <= 8
        and all(isinstance(code, str) and code in _WARNING_CODES for code in warnings)
    )
    if not valid_warnings:
        return False
    if capture_time is None:
        return (
            value["source_field"] is None
            and value["source_value"] is None
            and value["timezone_state"] == "not_applicable"
            and "capture_time_missing" in warnings
        )
    return (
        value["source_field"] in _SOURCE_FIELDS
        and value["timezone_state"] in {"explicit", "inferred_none"}
        and "capture_time_missing" not in warnings
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or _NORMALIZED_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_source_value(value: object) -> bool:
    if not isinstance(value, str) or _EXIF_SOURCE_VALUE.fullmatch(value) is None:
        return False
    if settings.PHOTO_PROCESSING_WORKER_TOKEN and settings.PHOTO_PROCESSING_WORKER_TOKEN in value:
        return False
    try:
        datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return False
    return True


def _safe_error_detail(value: str) -> bool:
    return (
        "\x00" not in value
        and _SECRET_MARKER.search(value) is None
        and settings.PHOTO_PROCESSING_WORKER_TOKEN not in value
    )


def _safe_worker_build(value: object) -> bool:
    return (
        isinstance(value, str)
        and _WORKER_BUILD.fullmatch(value) is not None
        and _safe_durable_string(value)
    )


def _safe_durable_string(value: str) -> bool:
    configured = settings.PHOTO_PROCESSING_WORKER_TOKEN
    return bool(
        "\x00" not in value
        and not any(character.isspace() for character in value)
        and _SECRET_MARKER.search(value) is None
        and (not configured or configured not in value)
    )


def _error(code: str, message: str, *, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _invalid_request() -> JsonResponse:
    return _error("invalid_request", "The request is invalid.", status=400)


def _invalid_result() -> JsonResponse:
    return _error("invalid_result", "The result is invalid.", status=400)


def _not_found() -> JsonResponse:
    return _error("attempt_not_found", "The processing attempt was not found.", status=404)


def _lease_not_current() -> JsonResponse:
    return _error("lease_not_current", "The processing lease is no longer current.", status=409)


def _completion_conflict() -> JsonResponse:
    return _error(
        "completion_conflict", "A different terminal result was already recorded.", status=409
    )


def _audit_conflict(conflict: CompletionConflict) -> None:
    if conflict.attempt_id is None or not conflict.submitted_hash:
        return
    attempt = (
        ProcessingAttempt.objects.filter(pk=conflict.attempt_id).only("event_id", "job_id").first()
    )
    if attempt is not None:
        ProcessingConflictAudit.objects.create(
            attempt_id=attempt.pk,
            event_id=attempt.event_id,
            job_id=attempt.job_id,
            received_at=timezone.now(),
            submitted_hash=conflict.submitted_hash,
            code="terminal_conflict",
        )
