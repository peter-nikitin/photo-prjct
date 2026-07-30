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
from processing.contracts import (
    CAPTURE_METADATA_CONTRACT,
    FACE_EMBEDDING_CONTRACT,
    GENERATE_PREVIEW_CONTRACT,
    PREVIEW_FACE_EMBEDDING_CONTRACT,
    ClaimedJob,
    CompletionConflict,
    EmptyClaim,
)
from processing.models import PhotoDerivative, ProcessingAttempt, ProcessingConflictAudit
from processing.services.jobs import (
    complete_attempt,
    fail_attempt,
    heartbeat_attempt,
    recover_expired_attempts,
)
from processing.services.jobs import (
    refresh_download as refresh_download_attempt,
)
from processing.services.previews import complete_preview_attempt
from processing.storage import ExactObjectDownloadStorage, ExactPreviewStorage, PreviewUploadGrant

_WORKER_BUILD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_NORMALIZED_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_SECRET_MARKER = re.compile(r"(?:[a-z][a-z0-9+.-]*://|x-amz-|signature=|credential=|token=)", re.I)
_SOURCE_FIELDS = {"DateTime", "DateTimeDigitized", "DateTimeOriginal"}
_EXIF_SOURCE_VALUE = re.compile(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")
_CAPTURE_METADATA_WARNINGS = {
    "capture_time_conflicting",
    "capture_time_malformed",
    "capture_time_missing",
}
_FACE_RESULT_WARNING_CODES = {
    "all_faces_filtered",
    "face_embedding_failed",
    "faces_truncated",
    "low_quality_face",
    "no_face_detected",
    "multiple_faces_detected",
    "no_faces_detected",
    "no_valid_faces",
    "edge_face",
}
_CAPTURE_METADATA_FAILURES = {
    "decode_failed": (False, "The image could not be decoded."),
    "download_authorization_expired": (True, "Download authorization expired."),
    "fingerprint_mismatch": (False, "The downloaded input did not match its fingerprint."),
    "input_too_large": (False, "The input exceeded its declared limit."),
    "network_interruption": (True, "A temporary network interruption occurred."),
    "storage_unavailable": (True, "Object storage is temporarily unavailable."),
    "unsupported_input": (False, "The input is unsupported."),
}
_FACE_EMBEDDING_FAILURES = {
    "decode_failed": (False, "The image could not be decoded."),
    "input_too_large": (False, "The input exceeded its declared limit."),
    "model_inference_error": (True, "The face model inference failed."),
    "model_inference_timeout": (True, "The face model processing timed out."),
    "network_interruption": (True, "A temporary network interruption occurred."),
    "no_face_detected": (False, "No face was detected in the image."),
    "storage_unavailable": (True, "Object storage is temporarily unavailable."),
    "timeout": (True, "The face model processing timed out."),
    "all_faces_filtered": (False, "All detected faces were filtered before embedding."),
}
_GENERATE_PREVIEW_FAILURES = {
    "decode_failed": (False, "The image could not be decoded."),
    "download_authorization_expired": (True, "Download authorization expired."),
    "fingerprint_mismatch": (False, "The downloaded input did not match its fingerprint."),
    "input_too_large": (False, "The input exceeded its declared limit."),
    "invalid_dimensions": (False, "The image dimensions are invalid."),
    "network_interruption": (True, "A temporary network interruption occurred."),
    "normalization_failed": (False, "The preview could not be normalized."),
    "output_contract_violation": (False, "The preview output violated its contract."),
    "storage_unavailable": (True, "Object storage is temporarily unavailable."),
    "unsupported_input": (False, "The input is unsupported."),
}
_GENERATE_PREVIEW_WARNING_CODES = {"color_profile_missing"}
_PROCESSOR_FAILURES = {
    "capture_metadata": _CAPTURE_METADATA_FAILURES,
    "face_embedding": _FACE_EMBEDDING_FAILURES,
    "generate_preview": _GENERATE_PREVIEW_FAILURES,
}
_PROCESSOR_RESULT_WARNINGS = {
    "capture_metadata": _CAPTURE_METADATA_WARNINGS,
    "face_embedding": _FACE_RESULT_WARNING_CODES,
    "generate_preview": _GENERATE_PREVIEW_WARNING_CODES,
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
        attempt = ProcessingAttempt.objects.only("processor_type").get(pk=parsed_attempt_id)
        completion_kwargs = {
            "result": data["result"],
            "download_duration_ms": data["download_ms"],
            "compute_duration_ms": data["compute_ms"],
            "total_duration_ms": data["total_ms"],
            "worker_started_at": data["started_at"],
            "worker_finished_at": data["finished_at"],
        }
        if attempt.processor_type == GENERATE_PREVIEW_CONTRACT.processor_type:
            completion = complete_preview_attempt(parsed_attempt_id, **completion_kwargs)
        else:
            completion = complete_attempt(parsed_attempt_id, **completion_kwargs)
    except ProcessingAttempt.DoesNotExist:
        return _not_found()
    except StorageUnavailable:
        return _error(
            "storage_unavailable", "Object storage is temporarily unavailable.", status=503
        )
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
            error_detail=_processor_error_detail(data["processor_type"], data["error_code"]),
            canonical_error_detail=data["error_detail"],
            retryable=_processor_retryable(data["processor_type"], data["error_code"]),
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
        fingerprint = _input_fingerprint(
            claimed.job.input_fingerprint, contract_version=claimed.job.contract_version
        )
        grant = ExactObjectDownloadStorage().create_download_grant(
            final_key=_fingerprint_key(fingerprint),
            max_ttl_seconds=_remaining_lease(claimed.attempt),
        )
        _validate_grant_lease(claimed.attempt, grant.expires_at)
        preview_upload_grant = None
        if claimed.job.processor_type == GENERATE_PREVIEW_CONTRACT.processor_type:
            preview_upload_grant = ExactPreviewStorage().create_upload_grant(
                staging_key=_preview_staging_key(str(claimed.attempt.id)),
                max_ttl_seconds=_remaining_lease(claimed.attempt),
            )
            _validate_grant_lease(claimed.attempt, preview_upload_grant.expires_at)
        return _claimed_payload(
            claimed,
            grant.url,
            grant.expires_at,
            fingerprint,
            preview_upload_grant=preview_upload_grant,
        )


def _refresh_with_grant(attempt_id: UUID) -> dict[str, object] | None:
    """Revalidate and sign under the same outer transaction so recovery cannot interleave."""
    with transaction.atomic():
        attempt = refresh_download_attempt(attempt_id)
        if attempt is None:
            return None
        fingerprint = _input_fingerprint(
            attempt.input_fingerprint, contract_version=attempt.contract_version
        )
        grant = ExactObjectDownloadStorage().create_download_grant(
            final_key=_fingerprint_key(fingerprint),
            max_ttl_seconds=_remaining_lease(attempt),
        )
        _validate_grant_lease(attempt, grant.expires_at)
        return {
            "attempt": _attempt_payload(attempt),
            "download_url": grant.url,
            "download_expires_at": grant.expires_at.isoformat(),
        }


def _input_fingerprint(value: object, *, contract_version: int) -> dict[str, int | str | None]:
    if not isinstance(value, dict):
        raise FingerprintInvariant()
    if contract_version == 2:
        fields = {
            "object_key",
            "object_size",
            "object_content_type",
            "object_etag",
            "media_kind",
            "pixel_width",
            "pixel_height",
        }
        if set(value) != fields:
            raise FingerprintInvariant()
        key = value["object_key"]
        size = value["object_size"]
        content_type = value["object_content_type"]
        etag = value["object_etag"]
        if not (
            isinstance(key, str)
            and (
                re.fullmatch(r"originals/[0-9a-f]{32}", key)
                or re.fullmatch(
                    r"derivatives/previews/[A-Za-z0-9_-]{1,32}/preview-small-v1/"
                    r"[0-9a-f-]{36}-[0-9a-f]{64}\.jpg",
                    key,
                )
            )
            and isinstance(size, int)
            and not isinstance(size, bool)
            and size >= 1
            and content_type == "image/jpeg"
            and (etag is None or isinstance(etag, str))
            and value["media_kind"] in {"original", "preview-small-v1"}
            and _positive_int(value["pixel_width"])
            and _positive_int(value["pixel_height"])
        ):
            raise FingerprintInvariant()
        return cast(dict[str, int | str | None], value)
    if contract_version != 1:
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


def _fingerprint_key(fingerprint: dict[str, int | str | None]) -> str:
    return cast(str, fingerprint.get("object_key", fingerprint.get("original_key")))


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
    claimed: ClaimedJob,
    url: str,
    expires_at: datetime,
    fingerprint: dict[str, int | str | None],
    preview_upload_grant: PreviewUploadGrant | None = None,
) -> dict[str, object]:
    job = claimed.job
    attempt = claimed.attempt
    payload: dict[str, object] = {
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
                "max_bytes": fingerprint.get("object_size", fingerprint.get("original_size")),
                "content_type": fingerprint.get(
                    "object_content_type", fingerprint.get("original_content_type")
                ),
            },
            "lease_expires_at": attempt.lease_expires_at.isoformat(),
            "download_url": url,
            "download_expires_at": expires_at.isoformat(),
        },
    }
    if job.processor_type == GENERATE_PREVIEW_CONTRACT.processor_type:
        if preview_upload_grant is None:
            raise FingerprintInvariant()
        payload["job"] = cast(dict[str, object], payload["job"]) | {
            "output_slots": [_preview_output_slot(str(attempt.id), preview_upload_grant)]
        }
    elif (
        job.contract_version == PREVIEW_FACE_EMBEDDING_CONTRACT.contract_version
        and job.processor_type == PREVIEW_FACE_EMBEDDING_CONTRACT.processor_type
        and job.processor_version == PREVIEW_FACE_EMBEDDING_CONTRACT.processor_version
    ):
        derivative = PhotoDerivative.objects.get(photo_id=job.photo_id, variant="preview-small-v1")
        payload["job"] = cast(dict[str, object], payload["job"]) | {
            "input_geometry": {
                "coordinate_space": "preview-small-v1",
                "pixel_width": derivative.width,
                "pixel_height": derivative.height,
                "oriented_source_width": derivative.oriented_source_width,
                "oriented_source_height": derivative.oriented_source_height,
            }
        }
    return payload


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


def _processor_contract(processor_type: str, contract_version: int, processor_version: int) -> bool:
    return (contract_version, processor_type, processor_version) in {
        (
            CAPTURE_METADATA_CONTRACT.contract_version,
            CAPTURE_METADATA_CONTRACT.processor_type,
            CAPTURE_METADATA_CONTRACT.processor_version,
        ),
        (
            FACE_EMBEDDING_CONTRACT.contract_version,
            FACE_EMBEDDING_CONTRACT.processor_type,
            FACE_EMBEDDING_CONTRACT.processor_version,
        ),
        (
            GENERATE_PREVIEW_CONTRACT.contract_version,
            GENERATE_PREVIEW_CONTRACT.processor_type,
            GENERATE_PREVIEW_CONTRACT.processor_version,
        ),
        (
            PREVIEW_FACE_EMBEDDING_CONTRACT.contract_version,
            PREVIEW_FACE_EMBEDDING_CONTRACT.processor_type,
            PREVIEW_FACE_EMBEDDING_CONTRACT.processor_version,
        ),
    }


def _processor_failures(processor_type: str) -> dict[str, tuple[bool, str]] | None:
    return _PROCESSOR_FAILURES.get(processor_type)


def _processor_error_detail(processor_type: str, error_code: str) -> str:
    failures = _processor_failures(processor_type)
    if failures is None:
        return ""
    return failures[error_code][1]


def _processor_retryable(processor_type: str, error_code: str) -> bool:
    failures = _processor_failures(processor_type)
    return bool(failures[error_code][0]) if failures is not None else False


def _valid_failure_code(processor_type: str, value: object) -> bool:
    failures = _processor_failures(processor_type)
    return isinstance(value, str) and failures is not None and value in failures


def _valid_warning_code(processor_type: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    warnings = _PROCESSOR_RESULT_WARNINGS.get(processor_type)
    return value in warnings if warnings is not None else False


def _valid_envelope(data: dict[str, Any], attempt_id: UUID, *, outcome: str) -> bool:
    try:
        attempt = ProcessingAttempt.objects.select_related("job").get(pk=attempt_id)
    except ProcessingAttempt.DoesNotExist:
        return False
    if not _processor_contract(
        attempt.processor_type, attempt.contract_version, attempt.processor_version
    ):
        return False
    failures = _processor_failures(attempt.processor_type)
    if failures is None:
        return False
    started_at = _parse_timestamp(data["started_at"])
    finished_at = _parse_timestamp(data["finished_at"])
    if not (
        data["outcome"] == outcome
        and data["attempt_id"] == str(attempt.id)
        and data["job_id"] == str(attempt.job_id)
        and type(data["contract_version"]) is int
        and data["contract_version"] == attempt.contract_version
        and type(data["processor_type"]) is str
        and data["processor_type"] == attempt.processor_type
        and type(data["processor_version"]) is int
        and data["processor_version"] == attempt.processor_version
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
        if not _valid_result(data["result"], attempt.processor_type, attempt.contract_version):
            return False
        return not (
            attempt.contract_version == PREVIEW_FACE_EMBEDDING_CONTRACT.contract_version
            and attempt.processor_type == PREVIEW_FACE_EMBEDDING_CONTRACT.processor_type
            and attempt.processor_version == PREVIEW_FACE_EMBEDDING_CONTRACT.processor_version
        ) or _matches_accepted_preview_geometry(attempt, data["result"])
    return (
        _valid_failure_code(attempt.processor_type, data["error_code"])
        and type(data["retryable"]) is bool
        and data["retryable"] == failures[data["error_code"]][0]
        and isinstance(data["error_detail"], str)
        and len(data["error_detail"]) <= 512
        and _safe_error_detail(data["error_detail"])
    )


def _valid_result(value: object, processor_type: str, contract_version: int) -> bool:
    if processor_type == CAPTURE_METADATA_CONTRACT.processor_type:
        return _valid_capture_metadata_result(value)
    if processor_type == FACE_EMBEDDING_CONTRACT.processor_type:
        return _valid_face_embedding_result(value, contract_version=contract_version)
    if processor_type == GENERATE_PREVIEW_CONTRACT.processor_type:
        return _valid_preview_result(value)
    return False


def _valid_capture_metadata_result(value: object) -> bool:
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
        and all(
            isinstance(code, str)
            and _valid_warning_code(CAPTURE_METADATA_CONTRACT.processor_type, code)
            for code in warnings
        )
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


def _valid_face_embedding_result(value: object, *, contract_version: int = 1) -> bool:
    if not isinstance(value, dict):
        return False
    if not ("face_count" in value and "faces" in value and "warnings" in value):
        return False
    if value.get("timings") is not None and not isinstance(value["timings"], dict):
        return False
    if not (
        isinstance(value["face_count"], int)
        and not isinstance(value["face_count"], bool)
        and 0 <= value["face_count"] <= 1_000
    ):
        return False
    if not _safe_face_model(value.get("model", "sface")):
        return False
    faces = value["faces"]
    if not (isinstance(faces, list) and len(faces) <= 1_024 and len(faces) == value["face_count"]):
        return False
    warnings = value["warnings"]
    if not (
        isinstance(warnings, list)
        and len(warnings) <= 8
        and all(_valid_face_warning(code) for code in warnings)
    ):
        return False
    has_single_query_face_usable = value.get("has_single_query_face_usable")
    if has_single_query_face_usable is not None and not isinstance(
        has_single_query_face_usable, bool
    ):
        return False
    if contract_version == 2:
        geometry = value.get("input_geometry")
        if not (
            isinstance(geometry, dict)
            and set(geometry)
            == {
                "coordinate_space",
                "pixel_width",
                "pixel_height",
                "oriented_source_width",
                "oriented_source_height",
            }
            and geometry["coordinate_space"] == "preview-small-v1"
            and all(_positive_int(geometry[name]) for name in set(geometry) - {"coordinate_space"})
        ):
            return False
    return all(_valid_face_embedding_record(face) for face in faces)


def _matches_accepted_preview_geometry(attempt: ProcessingAttempt, result: object) -> bool:
    if not isinstance(result, dict):
        return False
    derivative = PhotoDerivative.objects.filter(
        photo_id=attempt.photo_id, variant="preview-small-v1", accepted_attempt_id__isnull=False
    ).first()
    if derivative is None:
        return False
    return result.get("input_geometry") == {
        "coordinate_space": "preview-small-v1",
        "pixel_width": derivative.width,
        "pixel_height": derivative.height,
        "oriented_source_width": derivative.oriented_source_width,
        "oriented_source_height": derivative.oriented_source_height,
    }


def _valid_preview_result(value: object) -> bool:
    fields = {
        "variant",
        "content_type",
        "byte_size",
        "width",
        "height",
        "oriented_source_width",
        "oriented_source_height",
        "sha256",
        "upload_ms",
        "warnings",
    }
    return (
        isinstance(value, dict)
        and set(value) == fields
        and value["variant"] == "preview-small-v1"
        and value["content_type"] == "image/jpeg"
        and _positive_int(value["byte_size"])
        and value["byte_size"] <= 10_485_760
        and _positive_int(value["width"])
        and _positive_int(value["height"])
        and max(value["width"], value["height"]) <= 1600
        and _positive_int(value["oriented_source_width"])
        and _positive_int(value["oriented_source_height"])
        and isinstance(value["sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
        and _duration(value["upload_ms"])
        and isinstance(value["warnings"], list)
        and len(value["warnings"]) <= 8
        and all(_valid_warning_code("generate_preview", item) for item in value["warnings"])
    )


def _preview_staging_key(attempt_id: str) -> str:
    return f"processing-staging/previews/{attempt_id}/preview-small-v1.jpg"


def _preview_output_slot(attempt_id: str, grant: PreviewUploadGrant) -> dict[str, object]:
    """One short-lived PUT grant for the current preview attempt's exact staging key."""
    return {
        "variant": "preview-small-v1",
        "upload_url": grant.url,
        "upload_expires_at": grant.expires_at.isoformat(),
        "content_type": "image/jpeg",
        "staging_key": _preview_staging_key(attempt_id),
        "max_bytes": 10_485_760,
        "max_width": 1600,
        "max_height": 1600,
        "checksum_algorithm": "sha256",
    }


def _safe_face_model(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value))
        and _safe_durable_string(value)
    )


def _valid_face_warning(value: object) -> bool:
    return _valid_warning_code(FACE_EMBEDDING_CONTRACT.processor_type, value)


def _valid_face_embedding_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value).issuperset({"face_id", "bbox", "quality", "embedding_sha256"}):
        if not (
            isinstance(value["face_id"], str)
            and 0 < len(value["face_id"]) <= 64
            and _safe_durable_string(value["face_id"])
        ):
            return False
        if not (
            isinstance(value["bbox"], list)
            and len(value["bbox"]) == 4
            and all(_safe_face_coordinate(item) for item in value["bbox"])
        ):
            return False
        quality = value["quality"]
        if not (isinstance(quality, (int, float)) and 0.0 <= quality <= 1.0):
            return False
        return (
            isinstance(value["embedding_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["embedding_sha256"]) is not None
        )
    if not {"index", "bbox", "confidence", "landmarks", "embedding"} <= set(value):
        return False
    if not (
        isinstance(value["index"], int)
        and value["index"] >= 0
        and not isinstance(value["index"], bool)
        and _valid_face_bbox(value["bbox"])
    ):
        return False
    if not (isinstance(value["confidence"], (int, float)) and 0.0 <= value["confidence"] <= 1.0):
        return False
    landmarks = value["landmarks"]
    if not (
        isinstance(landmarks, list)
        and len(landmarks) == 5
        and all(
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(coord, (int, float)) for coord in point)
            for point in landmarks
        )
    ):
        return False
    embedding = value["embedding"]
    if not (
        isinstance(embedding, list)
        and len(embedding) <= 512
        and all(isinstance(item, (int, float)) for item in embedding)
    ):
        return False
    return True


def _valid_face_bbox(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(_safe_face_coordinate(item) for item in value)
    )


def _safe_face_coordinate(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 10_000


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
