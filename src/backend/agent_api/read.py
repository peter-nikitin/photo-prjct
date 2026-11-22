from collections.abc import Callable
from uuid import UUID

from django.conf import settings
from django.db.models import Count, Model, QuerySet
from django.http import HttpRequest, JsonResponse
from ingestion.models import UploadBatch, UploadItem
from picflow.models import Event, EventFolder
from processing.models import PhotoProcessingState

from agent_api.auth import error


def whoami(request: HttpRequest) -> JsonResponse:
    user = request.user
    return JsonResponse(
        {
            "user": {"id": user.pk, "username": user.get_username()},
            "upload": {
                "enabled": settings.PHOTO_UPLOAD_ENABLED
                and user.has_perm("ingestion.upload_photos"),
                "max_files": settings.PHOTO_UPLOAD_MAX_FILES,
                "max_file_bytes": settings.PHOTO_UPLOAD_MAX_FILE_BYTES,
                "registration_chunk": settings.PHOTO_UPLOAD_REGISTRATION_CHUNK,
            },
        }
    )


def event_fields(event: Event) -> dict[str, object]:
    return {
        "id": event.pk,
        "name": event.name,
        "city": event.city,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "publication_status": event.publication_status,
    }


def events(request: HttpRequest) -> JsonResponse:
    if not request.user.has_perm("picflow.view_event"):
        return denied()
    return page(request, Event.objects.order_by("pk"), event_fields)


def event_detail(request: HttpRequest, event: int) -> JsonResponse:
    if not request.user.has_perm("picflow.view_event"):
        return denied()
    row = Event.objects.filter(pk=event).first()
    return JsonResponse({"event": event_fields(row)}) if row else missing()


def folders(request: HttpRequest, event: int) -> JsonResponse:
    if not request.user.has_perms(("picflow.view_event", "picflow.view_eventfolder")):
        return denied()
    if not Event.objects.filter(pk=event).exists():
        return missing()
    return page(
        request,
        EventFolder.objects.filter(event_id=event).order_by("pk"),
        lambda folder: {"id": folder.pk, "event_id": folder.event_id, "name": folder.name},
    )


def owned_batches(request: HttpRequest) -> QuerySet[UploadBatch]:
    return UploadBatch.objects.filter(uploader_id=request.user.pk)


def batch_fields(batch: UploadBatch) -> dict[str, object]:
    return {
        "id": str(batch.pk),
        "event_id": batch.event_id,
        "status": batch.status,
        "expected_item_count": batch.expected_item_count,
        "created_at": batch.created_at,
        "completed_at": batch.completed_at,
    }


def batches(request: HttpRequest) -> JsonResponse:
    if not request.user.has_perm("ingestion.upload_photos"):
        return denied()
    return page(request, owned_batches(request).order_by("-created_at", "pk"), batch_fields)


def batch_detail(request: HttpRequest, batch: UUID) -> JsonResponse:
    if not request.user.has_perm("ingestion.upload_photos"):
        return denied()
    row = owned_batches(request).filter(pk=batch).first()
    if row is None:
        return missing()
    counts = {
        entry["status"]: entry["count"]
        for entry in row.items.values("status").annotate(count=Count("pk"))
    }
    confirmed = counts.get(UploadItem.Status.UPLOADED, 0)
    failed = counts.get(UploadItem.Status.FAILED, 0)
    processing = list(
        PhotoProcessingState.objects.filter(photo__upload_item__batch=row)
        .values("processor_type", "status")
        .annotate(count=Count("pk"))
        .order_by("processor_type", "status")
    )
    return JsonResponse(
        {
            "batch": batch_fields(row),
            "originals": {
                "confirmed": confirmed,
                "failed": failed,
                "unresolved": row.expected_item_count - confirmed - failed,
            },
            "processing": processing,
        }
    )


def item_fields(item: UploadItem) -> dict[str, object]:
    return {
        "id": str(item.pk),
        "client_item_id": str(item.client_item_id),
        "filename": item.original_filename,
        "folder_id": item.folder_id,
        "status": item.status,
        "photo_id": item.photo_id,
        "error_code": item.error_code,
        "upload_attempts": item.upload_attempts,
    }


def items(request: HttpRequest, batch: UUID) -> JsonResponse:
    if not request.user.has_perm("ingestion.upload_photos"):
        return denied()
    row = owned_batches(request).filter(pk=batch).first()
    if row is None:
        return missing()
    return page(request, row.items.order_by("pk"), item_fields)


def item_detail(request: HttpRequest, batch: UUID, item: UUID) -> JsonResponse:
    if not request.user.has_perm("ingestion.upload_photos"):
        return denied()
    row = UploadItem.objects.filter(
        pk=item, batch_id=batch, batch__uploader_id=request.user.pk
    ).first()
    return JsonResponse({"item": item_fields(row)}) if row else missing()


def page[T: Model](
    request: HttpRequest, queryset: QuerySet[T], serialize: Callable[[T], dict[str, object]]
) -> JsonResponse:
    try:
        number = int(request.GET.get("page", "1"))
        if not 1 <= number <= 100_000:
            raise ValueError
    except ValueError:
        return error("invalid_page", "Page must be an integer between 1 and 100000.", 400)
    size = 100
    start = (number - 1) * size
    rows = list(queryset[start : start + size + 1])
    return JsonResponse(
        {
            "results": [serialize(row) for row in rows[:size]],
            "next_page": number + 1 if len(rows) > size and number < 100_000 else None,
        }
    )


def denied() -> JsonResponse:
    return error("permission_denied", "You do not have permission to read this resource.", 403)


def missing() -> JsonResponse:
    return error("not_found", "The resource was not found.", 404)
