"""Role-scoped polling payload for the private event photo workspace."""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from ingestion.models import UploadBatch
from ingestion.services.batch_history import (
    OwnedBatchHistory,
    owned_event_batch_summaries,
)
from processing.photo_status import photo_processing_details, summarize_photo_processing

from picflow.event_management import event_photo_page, event_photo_queryset
from picflow.event_management_access import (
    can_inspect_event_photos,
    can_upload_event_photos,
    event_management_denial,
)
from picflow.event_management_forms import EventPhotoFilterForm
from picflow.models import Event, Photo

MAX_DISPLAYED_PHOTOS = 100
MAX_DISPLAYED_BATCHES = 20
_ACTIVE_BATCH_STATUSES = {
    UploadBatch.Status.CREATED,
    UploadBatch.Status.UPLOADING,
}


def _displayed_photo_ids(request: HttpRequest) -> tuple[str, ...]:
    values = request.GET.getlist("photo_id")
    if len(values) > MAX_DISPLAYED_PHOTOS or any(
        not value or len(value) > Photo._meta.pk.max_length for value in values
    ):
        raise ValueError
    return tuple(dict.fromkeys(values))


def _displayed_batch_ids(request: HttpRequest) -> tuple[UUID, ...]:
    values = request.GET.getlist("batch_id")
    if len(values) > MAX_DISPLAYED_BATCHES:
        raise ValueError
    try:
        return tuple(dict.fromkeys(UUID(value) for value in values))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError from error


def _include_results(request: HttpRequest) -> bool:
    values = request.GET.getlist("include_results")
    if not values:
        return True
    if values == ["0"]:
        return False
    if values == ["1"]:
        return True
    raise ValueError


def _batch_payload(row: OwnedBatchHistory) -> dict[str, object]:
    payload = asdict(row)
    payload["id"] = str(row.id)
    payload["created_at"] = row.created_at.isoformat()
    payload["has_active_work"] = bool(
        (row.status in _ACTIVE_BATCH_STATUSES and not row.can_close)
        or row.processing["has_active_work"]
    )
    return payload


@never_cache
@require_GET
def event_management_status(request: HttpRequest, event_id: int) -> HttpResponse:
    """Return bounded status data after applying the requester's server-side role scope."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    denial = event_management_denial(request)
    if denial is not None:
        return denial
    try:
        photo_ids = _displayed_photo_ids(request)
        batch_ids = _displayed_batch_ids(request)
        include_results = _include_results(request)
    except ValueError:
        return JsonResponse({"error": "invalid_displayed_ids"}, status=400)
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return HttpResponse(status=404)

    can_inspect = can_inspect_event_photos(request.user)
    can_upload = can_upload_event_photos(request.user)
    payload: dict[str, object] = {
        "server_timestamp": timezone.now().isoformat(),
        "has_active_work": False,
        "capabilities": {
            "can_inspect": can_inspect,
            "can_upload": can_upload,
        },
    }
    has_active_work = False

    if can_inspect:
        event_photos = Photo.objects.filter(event=event)
        summary = summarize_photo_processing(event_photos)
        has_active_work = summary["has_active_work"]
        admin_payload: dict[str, object] = {"summary": summary}
        if include_results:
            filter_form = EventPhotoFilterForm(event, request.GET)
            if not filter_form.is_valid():
                return JsonResponse({"error": "invalid_filters"}, status=400)
            filtered_photos = event_photo_queryset(event, filter_form.filters)
            current_page = event_photo_page(
                event,
                filter_form.filters,
                page=request.GET.get("page", "1"),
            )
            current_page_ids = tuple(photo.pk for photo in current_page)
            detail_rows = photo_processing_details(event_photos.filter(pk__in=photo_ids))
            details_by_id = {row["photo_id"]: row for row in detail_rows}
            admin_payload.update(
                {
                    "filtered_result_count": filtered_photos.count(),
                    "result_list_changed": current_page_ids != photo_ids,
                    "photos": [
                        details_by_id[photo_id]
                        for photo_id in photo_ids
                        if photo_id in details_by_id
                    ],
                }
            )
        payload["admin"] = admin_payload

    if can_upload:
        batch_summaries = owned_event_batch_summaries(
            uploader=request.user,
            event=event,
            batch_ids=batch_ids,
        )
        batch_rows = [_batch_payload(row) for row in batch_summaries]
        payload["batches"] = batch_rows
        has_active_work = has_active_work or any(bool(row["has_active_work"]) for row in batch_rows)

    payload["has_active_work"] = has_active_work
    return JsonResponse(payload)
