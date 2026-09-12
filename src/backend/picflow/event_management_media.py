"""Exact-object, admin-only delivery; never reuse public gallery eligibility."""

from pathlib import Path

from django.db.models import F, QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageError
from processing.models import GENERATE_PREVIEW_PROCESSOR, PhotoProcessingState, ProcessingAttempt

from picflow.event_management_access import event_management_denial
from picflow.models import Photo


def with_admin_thumbnail(photos: QuerySet[Photo]) -> QuerySet[Photo]:
    """Select accepted clean thumbnails within the caller's authorized photo scope."""
    return photos.filter(
        derivatives__variant="preview-small-v1",
        processing_states__processor_type=GENERATE_PREVIEW_PROCESSOR,
        processing_states__status=PhotoProcessingState.Status.SUCCEEDED,
        processing_states__accepted_attempt=F("derivatives__accepted_attempt"),
        processing_states__accepted_attempt__accepted=True,
        processing_states__accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
    )


@never_cache
@require_GET
def event_management_media(
    request: HttpRequest, event_id: int, photo_id: str, variant: str
) -> HttpResponse:
    denial = event_management_denial(request, admin_only=True)
    if denial is not None:
        return denial
    if variant not in {"thumbnail", "original"}:
        return HttpResponse(status=404)
    try:
        photo = Photo.objects.get(event_id=event_id, pk=photo_id)
    except Photo.DoesNotExist:
        return HttpResponse(status=404)

    if variant == "thumbnail":
        key = (
            with_admin_thumbnail(Photo.objects.filter(pk=photo.pk, event_id=event_id))
            .values_list("derivatives__final_key", flat=True)
            .first()
        )
        if not key:
            return HttpResponse(status=404)
    elif photo.original_key:
        key = photo.original_key
    else:
        return _legacy_original(photo)

    try:
        url = PrivateUploadStorage().sign_final(key=key)
    except ObjectMissing:
        return HttpResponse(status=404)
    except (StorageError, ValueError):
        return HttpResponse(status=503)
    response = HttpResponseRedirect(url)
    response["Referrer-Policy"] = "no-referrer"
    return response


def _legacy_original(photo: Photo) -> HttpResponse:
    content_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    content_type = content_types.get(Path(photo.src.name).suffix.lower())
    if not photo.src or content_type is None:
        return HttpResponse(status=404)
    try:
        source = photo.src.open("rb")
    except OSError:
        return HttpResponse(status=404)
    response = FileResponse(source, content_type=content_type)
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response
