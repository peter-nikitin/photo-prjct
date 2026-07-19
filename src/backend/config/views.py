from datetime import date
from typing import cast

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageError
from picflow.gallery import (
    GALLERY_VARIANTS,
    CloseableMediaIterator,
    GalleryPhotoFactory,
    GalleryVariant,
    PublicMediaResolver,
)
from picflow.models import Event, Photo


def health(request):  # noqa: ARG001
    return JsonResponse({"status": "ok"})


def event_catalog(request):
    today = date.today()
    published = Event.objects.published()
    upcoming = list(published.filter(end_date__gte=today).order_by("start_date", "name"))
    past = list(published.filter(end_date__lt=today).order_by("-start_date", "name"))
    return render(request, "catalog/event_catalog.html", {"events": [*upcoming, *past]})


def event_detail(request, slug: str):
    event = get_object_or_404(Event.objects.published(), slug=slug)
    gallery_photos = ()
    if event.access_type == Event.AccessType.FREE:
        gallery_photos = tuple(
            GalleryPhotoFactory.from_photo(photo=photo, event_slug=event.slug)
            for photo in Photo.objects.filter(event=event, src="", original_key__isnull=False)
            .select_related("event")
            .order_by("id")
        )
    return render(
        request,
        "catalog/event_detail.html",
        {"event": event, "gallery_photos": gallery_photos},
    )


def _public_media_resolver() -> PublicMediaResolver:
    return PublicMediaResolver(storage=PrivateUploadStorage())


@require_GET
def photo_media(request, slug: str, photo_id: str, variant: str) -> HttpResponse:  # noqa: ARG001
    if variant not in GALLERY_VARIANTS:
        return HttpResponse(status=404)
    event = get_object_or_404(
        Event.objects.published(), slug=slug, access_type=Event.AccessType.FREE
    )
    photo = get_object_or_404(Photo, pk=photo_id, event=event, src="", original_key__isnull=False)
    try:
        media = _public_media_resolver().resolve(photo=photo, variant=cast(GalleryVariant, variant))
    except ObjectMissing:
        return HttpResponse(status=404)
    except StorageError:
        return HttpResponse(status=503)
    stream = CloseableMediaIterator(media=media, event_slug=event.slug, photo_id=photo.pk)
    response = StreamingHttpResponse(stream, content_type=media.content_type)
    response["Content-Length"] = str(media.content_length)
    response["Content-Disposition"] = f'inline; filename="photo-{photo.pk}.{media.extension}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def legacy_events_redirect(request):  # noqa: ARG001
    return redirect("event_catalog")


def legal(request):
    return render(request, "ui/legal.html")
