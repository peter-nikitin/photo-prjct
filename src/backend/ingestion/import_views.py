"""Owned browser JSON endpoints for server-side photo imports."""

from __future__ import annotations

import json
import re
from functools import wraps
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_http_methods
from picflow.models import Event, EventFolder
from processing.models import PhotoProcessingState

from ingestion.import_contracts import (
    IMPORT_CONTRACT_VERSION,
    IMPORT_MAX_JSON_BYTES,
    IMPORT_MAX_PAGE_ITEMS,
    ImportBatchResponse,
    ImportErrorResponse,
)
from ingestion.import_worker_views import json_response
from ingestion.models import ImportBatch, ImportItem
from ingestion.services.imports import ImportConflict, create_import, retry_import_errors
from ingestion.views import _upload_access

_SOURCE_KEY = re.compile(r"[A-Za-z0-9_-]{1,512}")
_SUBMISSION_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MAX_SIGNED_BIGINT = (1 << 63) - 1
_MAX_POSITIVE_INTEGER = (1 << 31) - 1


def _import_mutation(view):  # noqa: ANN001, ANN201
    @wraps(view)
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not settings.PHOTO_IMPORT_ENABLED:
            return _not_found()
        return view(request, *args, **kwargs)

    return wrapped


@require_http_methods(["GET", "POST"])
@_upload_access(json_errors=True)
def import_collection(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        page = _pagination(request)
        if page is None:
            return _invalid_request()
        page_number, page_size = page
        queryset = _visible_batches(request).order_by("-created_at", "-id")
        return _batch_page(queryset, page_number=page_number, page_size=page_size)
    if not settings.PHOTO_IMPORT_ENABLED:
        return _not_found()
    data, error = _json_object(
        request,
        required={
            "contract_version",
            "event_id",
            "folder_id",
            "source_url",
            "submission_key",
        },
    )
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    event_id = data["event_id"]
    folder_id = data["folder_id"]
    source_key = _public_source_key(data["source_url"])
    submission_key = data["submission_key"]
    if (
        not _positive_int(event_id)
        or (folder_id is not None and not _positive_int(folder_id))
        or source_key is None
        or not isinstance(submission_key, str)
        or _SUBMISSION_KEY.fullmatch(submission_key) is None
    ):
        return _invalid_request()
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        return _not_found()
    folder = None
    if folder_id is not None:
        folder = EventFolder.objects.filter(pk=folder_id, event=event).first()
        if folder is None:
            return _not_found()
    replayed = ImportBatch.objects.filter(
        owner_id=request.user.pk,
        submission_key=submission_key,
    ).exists()
    try:
        result = create_import(
            actor=request.user,
            event=event,
            folder=folder,
            submitted_source_key=source_key,
            submission_key=submission_key,
        )
    except ImportConflict as exc:
        return _service_error(exc)
    batch = _batch_queryset().get(pk=result.id)
    return json_response(
        ImportBatchResponse(_batch_payload(batch)).payload(),
        status=200 if replayed else 201,
    )


@require_http_methods(["GET"])
@_upload_access(json_errors=True)
def import_detail(request: HttpRequest, batch: UUID) -> HttpResponse:
    record = _visible_batches(request).filter(pk=batch).first()
    if record is None:
        return _not_found()
    return json_response(ImportBatchResponse(_batch_payload(record)).payload())


@require_http_methods(["GET"])
@_upload_access(json_errors=True)
def import_items(request: HttpRequest, batch: UUID) -> HttpResponse:
    owner_batch = _visible_batches(request).filter(pk=batch).first()
    if owner_batch is None:
        return _not_found()
    page = _pagination(request)
    if page is None:
        return _invalid_request()
    page_number, page_size = page
    queryset = owner_batch.items.order_by("source_path", "id")
    total = queryset.count()
    offset = (page_number - 1) * page_size
    items = [_item_payload(item) for item in queryset[offset : offset + page_size]]
    return json_response(
        {
            "contract_version": IMPORT_CONTRACT_VERSION,
            "items": items,
            "pagination": _pagination_payload(total, page_number, page_size),
        }
    )


@require_http_methods(["POST"])
@_upload_access(json_errors=True)
@_import_mutation
def import_retry(request: HttpRequest, batch: UUID) -> HttpResponse:
    owner_batch = _visible_batches(request).filter(pk=batch).first()
    if owner_batch is None:
        return _not_found()
    data, error = _json_object(request, required={"contract_version"})
    if error is not None:
        return error
    assert data is not None
    if (error := _version_error(data)) is not None:
        return error
    try:
        actor = owner_batch.owner if request.user.is_superuser else request.user
        result = retry_import_errors(actor=actor, batch_id=batch)
    except ImportBatch.DoesNotExist:
        return _not_found()
    except ImportConflict as exc:
        return _service_error(exc)
    record = _batch_queryset().get(pk=result.id)
    return json_response(ImportBatchResponse(_batch_payload(record)).payload())


def _batch_queryset() -> QuerySet[ImportBatch]:
    active_processing = PhotoProcessingState.objects.filter(
        photo__import_items__batch_id=OuterRef("pk"),
        status__in=(
            PhotoProcessingState.Status.QUEUED,
            PhotoProcessingState.Status.PROCESSING,
            PhotoProcessingState.Status.RETRY_WAIT,
        ),
    )
    return ImportBatch.objects.select_related("owner", "event", "folder").annotate(
        imported_total=Count("items", filter=Q(items__status=ImportItem.Status.IMPORTED)),
        duplicate_total=Count("items", filter=Q(items__status=ImportItem.Status.DUPLICATE)),
        error_total=Count("items", filter=Q(items__status=ImportItem.Status.ERROR)),
        pending_total=Count(
            "items",
            filter=Q(
                items__status__in=(
                    ImportItem.Status.PENDING,
                    ImportItem.Status.CLAIMED,
                    ImportItem.Status.UPLOADING,
                )
            ),
        ),
        processing_active=Exists(active_processing),
    )


def _visible_batches(request: HttpRequest) -> QuerySet[ImportBatch]:
    queryset = _batch_queryset()
    if request.user.is_superuser:
        return queryset
    return queryset.filter(owner_id=request.user.pk)


def _batch_page(
    queryset: QuerySet[ImportBatch], *, page_number: int, page_size: int
) -> HttpResponse:
    total = queryset.count()
    offset = (page_number - 1) * page_size
    imports = [_batch_payload(batch) for batch in queryset[offset : offset + page_size]]
    return json_response(
        {
            "contract_version": IMPORT_CONTRACT_VERSION,
            "imports": imports,
            "pagination": _pagination_payload(total, page_number, page_size),
        }
    )


def _batch_payload(batch: ImportBatch) -> dict[str, object]:
    return {
        "id": str(batch.id),
        "status": batch.status,
        "event": {"id": batch.event_id, "name": batch.event.name},
        "folder": (
            {"id": batch.folder_id, "name": batch.folder.name} if batch.folder is not None else None
        ),
        "created_at": batch.created_at.isoformat(),
        "completed_at": batch.completed_at.isoformat() if batch.completed_at is not None else None,
        "counts": {
            "jpeg": batch.jpeg_count,
            "directory": batch.directory_count,
            "unsupported": batch.unsupported_count,
            "imported": getattr(batch, "imported_total", 0),
            "duplicate": getattr(batch, "duplicate_total", 0),
            "error": getattr(batch, "error_total", 0),
            "pending": getattr(batch, "pending_total", 0),
        },
        "error_code": batch.error_code,
        "processing_active": getattr(batch, "processing_active", False),
    }


def _item_payload(item: ImportItem) -> dict[str, object]:
    return {
        "id": str(item.id),
        "filename": item.filename,
        "byte_size": item.byte_size,
        "status": item.status,
        "error_code": item.error_code,
        "photo_id": item.photo_id,
    }


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


def _public_source_key(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"disk.yandex.ru", "yadi.sk"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    parts = parsed.path.split("/")
    if len(parts) not in {3, 4} or parts[0] != "" or parts[1] != "d":
        return None
    if len(parts) == 4 and parts[3] != "":
        return None
    key = unquote(parts[2])
    return key if _SOURCE_KEY.fullmatch(key) is not None else None


def _pagination(request: HttpRequest) -> tuple[int, int] | None:
    if set(request.GET) - {"page", "page_size"}:
        return None
    raw_page = request.GET.get("page", "1")
    raw_size = request.GET.get("page_size", "20")
    if not _bounded_decimal(raw_page, maximum=_MAX_POSITIVE_INTEGER) or not _bounded_decimal(
        raw_size, maximum=IMPORT_MAX_PAGE_ITEMS
    ):
        return None
    page = int(raw_page)
    page_size = int(raw_size)
    if page < 1 or not 1 <= page_size <= IMPORT_MAX_PAGE_ITEMS:
        return None
    return page, page_size


def _pagination_payload(total: int, page: int, page_size: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def _positive_int(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 < value <= _MAX_SIGNED_BIGINT
    )


def _bounded_decimal(value: str, *, maximum: int) -> bool:
    return value.isdecimal() and len(value) <= len(str(maximum)) and int(value) <= maximum


def _service_error(exc: ImportConflict) -> HttpResponse:
    if exc.code == "feature_paused":
        return _error("feature_paused", "Yandex Disk import is paused.", status=409)
    if exc.code == "permission_denied":
        return _error("permission_denied", "Photo upload permission is required.", status=403)
    safe_codes = {
        "folder_event_mismatch",
        "invalid_source",
        "invalid_submission_key",
        "nothing_to_retry",
        "submission_conflict",
    }
    code = exc.code if exc.code in safe_codes else "import_conflict"
    return _error(code, "The import operation conflicts with current state.", status=409)


def _error(code: str, message: str, *, status: int) -> HttpResponse:
    return json_response(ImportErrorResponse(code, message).payload(), status=status)


def _invalid_request() -> HttpResponse:
    return _error("invalid_request", "The request is invalid.", status=400)


def _not_found() -> HttpResponse:
    return _error("not_found", "The import resource was not found.", status=404)
