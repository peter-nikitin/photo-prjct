"""Strict, bounded JSON endpoints for the private photo-import worker."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from ingestion.import_auth import has_import_worker_token
from ingestion.import_contracts import (
    IMPORT_CONTRACT_VERSION,
    IMPORT_MAX_JSON_BYTES,
    IMPORT_MAX_PAGE_ITEMS,
    ImportBatchResponse,
    ImportCompletionResponse,
    ImportErrorResponse,
    ImportFailureResponse,
    LeaseResponse,
    ManifestPageResponse,
    UploadPreparationResponse,
    WorkerClaimResponse,
)
from ingestion.models import ImportAttempt, ImportBatch, ImportItem
from ingestion.services.import_publication import complete_import_item
from ingestion.services.imports import (
    ImportConflict,
    ImportResult,
    ManifestEntry,
    claim_import_work,
    finish_manifest,
    prepare_import_upload,
    record_import_failure,
    record_manifest_page,
    renew_import_lease,
)
from ingestion.storage import PrivateUploadStorage, StorageError

_FAILURE_OPERATIONS = {"manifest", "download", "upload", "publication"}
_FAILURE_CODES = {
    "download_timeout",
    "download_unavailable",
    "file_too_large",
    "hash_mismatch",
    "invalid_jpeg",
    "manifest_changed",
    "manifest_too_large",
    "publication_unavailable",
    "source_changed",
    "source_not_directory",
    "source_unavailable",
    "upload_unavailable",
}
_MAX_SIGNED_BIGINT = (1 << 63) - 1
_MAX_POSITIVE_INTEGER = (1 << 31) - 1


def json_response(payload: dict[str, object], *, status: int = 200) -> HttpResponse:
    encoded = json.dumps(
        payload,
        cls=DjangoJSONEncoder,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    limit = min(settings.PHOTO_IMPORT_MAX_JSON_BYTES, IMPORT_MAX_JSON_BYTES)
    if len(encoded) > limit:
        encoded = json.dumps(
            ImportErrorResponse(
                "response_too_large", "The response exceeds the API limit."
            ).payload(),
            separators=(",", ":"),
        ).encode()
        status = 500
    return HttpResponse(encoded, status=status, content_type="application/json")


def _endpoint(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @csrf_exempt
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if request.method != "POST":
            response = _error("method_not_allowed", "Only POST is allowed.", status=405)
            response["Allow"] = "POST"
            return response
        if not has_import_worker_token(request):
            return _error("worker_unauthorized", "Unauthorized.", status=401)
        return view(request, *args, **kwargs)

    return wrapped


@_endpoint
def readiness(request: HttpRequest) -> HttpResponse:
    data, error = _json_object(request, required={"contract_version"})
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    return json_response({"contract_version": IMPORT_CONTRACT_VERSION, "ready": True})


@_endpoint
def claim(request: HttpRequest) -> HttpResponse:
    data, error = _json_object(request, required={"contract_version", "lease_seconds"})
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    lease_seconds = data["lease_seconds"]
    if not _bounded_int(lease_seconds, minimum=1, maximum=300):
        return _invalid_request()
    try:
        claimed = claim_import_work(lease_seconds=lease_seconds)
    except (ImportConflict, ValueError) as exc:
        return _service_error(exc)
    if claimed.kind == "empty":
        return json_response(WorkerClaimResponse(work=None).payload())
    assert claimed.attempt_id is not None
    attempt = (
        ImportAttempt.objects.select_related("batch__scope", "item")
        .only(
            "id",
            "kind",
            "lease_expires_at",
            "batch_id",
            "batch__submitted_source_key",
            "batch__scope__canonical_source_key",
            "item_id",
            "item__source_path",
            "item__filename",
            "item__byte_size",
            "item__source_sha256",
            "item__source_md5",
            "item__source_version",
        )
        .get(pk=claimed.attempt_id)
    )
    source: dict[str, object]
    if attempt.kind == ImportAttempt.Kind.MANIFEST:
        source = {"key": attempt.batch.submitted_source_key}
    else:
        assert attempt.item is not None and attempt.batch.scope is not None
        source = {
            "key": attempt.batch.scope.canonical_source_key,
            "path": attempt.item.source_path,
            "name": attempt.item.filename,
            "size": attempt.item.byte_size,
            "sha256": attempt.item.source_sha256,
            "md5": attempt.item.source_md5,
            "version": attempt.item.source_version,
        }
    work = {
        "kind": claimed.kind,
        "batch_id": str(claimed.batch_id),
        "item_id": str(claimed.item_id) if claimed.item_id is not None else None,
        "attempt_id": str(claimed.attempt_id),
        "lease_expires_at": claimed.lease_expires_at.isoformat()
        if claimed.lease_expires_at is not None
        else None,
        "source": source,
    }
    return json_response(WorkerClaimResponse(work=work).payload())


@_endpoint
def renew(request: HttpRequest, attempt_id: UUID) -> HttpResponse:
    data, error = _json_object(request, required={"contract_version", "lease_seconds"})
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    lease_seconds = data["lease_seconds"]
    if not _bounded_int(lease_seconds, minimum=1, maximum=300):
        return _invalid_request()
    try:
        claimed = renew_import_lease(attempt_id, lease_seconds=lease_seconds)
    except (ImportAttempt.DoesNotExist, ImportBatch.DoesNotExist):
        return _not_found()
    except (ImportConflict, ValueError) as exc:
        return _service_error(exc)
    assert claimed.attempt_id is not None and claimed.lease_expires_at is not None
    assert claimed.kind in {"manifest", "file"}
    response = LeaseResponse(
        attempt_id=str(claimed.attempt_id),
        kind=cast(Literal["manifest", "file"], claimed.kind),
        lease_expires_at=claimed.lease_expires_at.isoformat(),
    )
    return json_response(response.payload())


@_endpoint
def manifest_page(request: HttpRequest, attempt_id: UUID) -> HttpResponse:
    data, error = _json_object(
        request,
        required={
            "contract_version",
            "batch_id",
            "page_number",
            "page_fingerprint",
            "entries",
        },
    )
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    batch_id = _uuid(data["batch_id"])
    page_number = data["page_number"]
    page_fingerprint = data["page_fingerprint"]
    raw_entries = data["entries"]
    entries = _manifest_entries(raw_entries)
    if (
        batch_id is None
        or not _bounded_int(page_number, minimum=0, maximum=_MAX_POSITIVE_INTEGER)
        or not _bounded_string(page_fingerprint, maximum=64)
        or entries is None
    ):
        return _invalid_request()
    if _has_duplicate_paths(entries):
        return _error(
            "manifest_changed", "The manifest contains repeated source paths.", status=409
        )
    try:
        result = record_manifest_page(
            batch_id=batch_id,
            attempt_id=attempt_id,
            page_number=page_number,
            page_fingerprint=page_fingerprint,
            entries=entries,
        )
    except (ImportAttempt.DoesNotExist, ImportBatch.DoesNotExist, ImportItem.DoesNotExist):
        return _not_found()
    except IntegrityError:
        return _error(
            "manifest_changed", "The manifest contains repeated source paths.", status=409
        )
    except ImportConflict as exc:
        return _service_error(exc)
    return json_response(ManifestPageResponse(result.page_number, result.replayed).payload())


@_endpoint
def manifest_finalize(request: HttpRequest, attempt_id: UUID) -> HttpResponse:
    data, error = _json_object(
        request,
        required={"contract_version", "batch_id", "canonical_source_key"},
    )
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    batch_id = _uuid(data["batch_id"])
    canonical_source_key = data["canonical_source_key"]
    if batch_id is None or not _bounded_string(canonical_source_key, maximum=512):
        return _invalid_request()
    try:
        result = finish_manifest(
            batch_id=batch_id,
            attempt_id=attempt_id,
            canonical_source_key=canonical_source_key,
        )
    except (ImportAttempt.DoesNotExist, ImportBatch.DoesNotExist):
        return _not_found()
    except ImportConflict as exc:
        return _service_error(exc)
    return json_response(ImportBatchResponse(_private_batch_payload(result)).payload())


@_endpoint
def prepare_upload(request: HttpRequest, attempt_id: UUID) -> HttpResponse:
    data, error = _json_object(
        request,
        required={
            "contract_version",
            "item_id",
            "content_sha256",
            "byte_size",
            "oriented_geometry",
        },
    )
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    item_id = _uuid(data["item_id"])
    sha256 = data["content_sha256"]
    byte_size = data["byte_size"]
    geometry = _geometry(data["oriented_geometry"])
    if (
        item_id is None
        or not _hex_string(sha256, length=64)
        or not _bounded_int(byte_size, minimum=1, maximum=52_428_800)
        or geometry is False
    ):
        return _invalid_request()
    try:
        result = prepare_import_upload(
            attempt_id=attempt_id,
            item_id=item_id,
            content_sha256=sha256,
            byte_size=byte_size,
            storage=PrivateUploadStorage(),
            oriented_geometry=geometry,
        )
    except (ImportAttempt.DoesNotExist, ImportBatch.DoesNotExist, ImportItem.DoesNotExist):
        return _not_found()
    except ImportConflict as exc:
        return _service_error(exc)
    except (StorageError, ValueError):
        return _error(
            "storage_unavailable", "Object storage is temporarily unavailable.", status=503
        )
    upload: dict[str, object] = {"status": result.status, "item_id": str(result.item_id)}
    if result.grant is not None:
        upload["grant"] = {
            "url": result.grant.url,
            "fields": result.grant.fields,
            "expires_at": result.grant.expires_at.isoformat(),
        }
    else:
        upload["grant"] = None
    return json_response(UploadPreparationResponse(upload).payload())


@_endpoint
def complete(request: HttpRequest, attempt_id: UUID) -> HttpResponse:
    data, error = _json_object(request, required={"contract_version", "item_id"})
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    item_id = _uuid(data["item_id"])
    if item_id is None:
        return _invalid_request()
    try:
        result = complete_import_item(
            attempt_id=attempt_id,
            item_id=item_id,
            storage=PrivateUploadStorage(),
        )
    except (ImportAttempt.DoesNotExist, ImportBatch.DoesNotExist, ImportItem.DoesNotExist):
        return _not_found()
    except ImportConflict as exc:
        return _service_error(exc)
    except StorageError:
        return _error(
            "storage_unavailable", "Object storage is temporarily unavailable.", status=503
        )
    return json_response(
        ImportCompletionResponse(
            item_id=str(result.item_id),
            status=result.status,
            photo_id=result.photo_id,
        ).payload()
    )


@_endpoint
def fail(request: HttpRequest, attempt_id: UUID) -> HttpResponse:
    data, error = _json_object(
        request,
        required={"contract_version", "operation", "code", "retryable"},
    )
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    operation = data["operation"]
    code = data["code"]
    retryable = data["retryable"]
    if (
        not isinstance(operation, str)
        or operation not in _FAILURE_OPERATIONS
        or not isinstance(code, str)
        or code not in _FAILURE_CODES
        or not isinstance(retryable, bool)
    ):
        return _invalid_request()
    try:
        result = record_import_failure(
            attempt_id=attempt_id,
            operation=cast(Literal["manifest", "download", "upload", "publication"], operation),
            code=code,
            retryable=retryable,
        )
    except (ImportAttempt.DoesNotExist, ImportBatch.DoesNotExist, ImportItem.DoesNotExist):
        return _not_found()
    except ImportConflict as exc:
        return _service_error(exc)
    return json_response(ImportFailureResponse(str(result.id), result.status).payload())


def _json_object(
    request: HttpRequest, *, required: set[str]
) -> tuple[dict[str, Any] | None, HttpResponse | None]:
    limit = min(settings.PHOTO_IMPORT_MAX_JSON_BYTES, IMPORT_MAX_JSON_BYTES)
    declared = request.headers.get("Content-Length")
    if declared is not None and not _bounded_decimal(declared, maximum=limit):
        return None, _invalid_request()
    try:
        raw = request.read(limit + 1)
        if len(raw) > limit:
            return None, _invalid_request()
        parsed = json.loads(raw or b"{}")
    except (RequestDataTooBig, ValueError, UnicodeDecodeError, RecursionError):
        return None, _invalid_request()
    if request.content_type != "application/json":
        return None, _invalid_request()
    if not isinstance(parsed, dict) or set(parsed) != required:
        return None, _invalid_request()
    return parsed, None


def _version_error(data: dict[str, Any]) -> HttpResponse | None:
    version = data.get("contract_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return _invalid_request()
    if version != IMPORT_CONTRACT_VERSION:
        return _error(
            "unsupported_contract", "The import contract version is unsupported.", status=400
        )
    return None


def _manifest_entries(value: object) -> tuple[ManifestEntry, ...] | None:
    if not isinstance(value, list) or len(value) > IMPORT_MAX_PAGE_ITEMS:
        return None
    parsed: list[ManifestEntry] = []
    required = {"path", "name", "kind", "size", "sha256", "md5", "version"}
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != required:
            return None
        kind = entry["kind"]
        size = entry["size"]
        sha256 = entry["sha256"]
        md5 = entry["md5"]
        if (
            not isinstance(kind, str)
            or kind not in {"jpeg", "directory", "unsupported"}
            or not _bounded_string(entry["path"], maximum=1024)
            or not _bounded_string(entry["name"], maximum=255)
            or not _bounded_string(entry["version"], maximum=255, allow_empty=True)
            or (size is not None and not _bounded_int(size, minimum=0, maximum=_MAX_SIGNED_BIGINT))
            or (kind == "jpeg" and not _bounded_int(size, minimum=1, maximum=_MAX_SIGNED_BIGINT))
            or (sha256 is not None and not _hex_string(sha256, length=64))
            or (md5 is not None and not _hex_string(md5, length=32))
        ):
            return None
        parsed.append(
            ManifestEntry(
                path=entry["path"],
                name=entry["name"],
                kind=cast(Literal["jpeg", "directory", "unsupported"], kind),
                size=size,
                sha256=sha256,
                md5=md5,
                version=entry["version"],
            )
        )
    return tuple(parsed)


def _has_duplicate_paths(entries: tuple[ManifestEntry, ...]) -> bool:
    paths = [entry.path for entry in entries]
    return len(paths) != len(set(paths))


def _geometry(value: object) -> tuple[int, int] | None | Literal[False]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        return False
    width = value["width"]
    height = value["height"]
    if not _bounded_int(width, minimum=1, maximum=_MAX_POSITIVE_INTEGER) or not _bounded_int(
        height, minimum=1, maximum=_MAX_POSITIVE_INTEGER
    ):
        return False
    return width, height


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _bounded_int(value: object, *, minimum: int, maximum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _bounded_string(value: object, *, maximum: int, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and len(value) <= maximum
        and "\x00" not in value
    )


def _bounded_decimal(value: str, *, maximum: int) -> bool:
    return value.isdecimal() and len(value) <= len(str(maximum)) and int(value) <= maximum


def _hex_string(value: object, *, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _private_batch_payload(result: ImportResult) -> dict[str, object]:
    return {
        "id": str(result.id),
        "status": result.status,
        "jpeg_count": result.jpeg_count,
        "directory_count": result.directory_count,
        "unsupported_count": result.unsupported_count,
        "imported_count": result.imported_count,
        "duplicate_count": result.duplicate_count,
        "error_count": result.error_count,
        "manifest_attempts": result.manifest_attempts,
    }


def _service_error(exc: Exception) -> HttpResponse:
    code = getattr(exc, "code", "invalid_request")
    if code == "storage_unavailable":
        return _error(
            "storage_unavailable",
            "Object storage is temporarily unavailable.",
            status=503,
            retryable=True,
        )
    if code in {"invalid_jpeg", "size_mismatch"}:
        return _error(
            code,
            "The uploaded object failed verification.",
            status=409,
            retryable=False,
        )
    if code == "stale_attempt":
        return _error("lease_not_current", "The import lease is no longer current.", status=409)
    if code in {"feature_paused", "permission_denied"}:
        return _error(code, "The import is paused.", status=409)
    safe_codes = {
        "attempt_kind_mismatch",
        "attempts_exhausted",
        "file_too_large",
        "invalid_geometry",
        "invalid_manifest_entry",
        "invalid_manifest_page",
        "invalid_sha256",
        "invalid_source",
        "item_not_claimed",
        "item_not_uploaded",
        "manifest_changed",
        "manifest_incomplete",
        "manifest_too_large",
        "missing_checkpoint",
        "promotion_conflict",
        "source_changed",
        "submission_conflict",
    }
    public_code = code if code in safe_codes else "import_conflict"
    return _error(public_code, "The import operation conflicts with current state.", status=409)


def _error(code: str, message: str, *, status: int, retryable: bool | None = None) -> HttpResponse:
    return json_response(
        ImportErrorResponse(code, message, retryable=retryable).payload(), status=status
    )


def _invalid_request() -> HttpResponse:
    return _error("invalid_request", "The request is invalid.", status=400)


def _not_found() -> HttpResponse:
    return _error("not_found", "The import resource was not found.", status=404)
