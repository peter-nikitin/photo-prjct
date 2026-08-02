from datetime import date

from django.conf import settings
from django.core.paginator import InvalidPage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET
from ingestion.storage import (
    ObjectMissing,
    PrivateUploadStorage,
    StorageError,
    StorageUnavailable,
)
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhoto,
    GalleryPhotoFactory,
    PublicMediaResolver,
    gallery_page,
    gallery_photo_queryset,
)
from picflow.models import Event
from selfie_search.forms import SelfieSearchUploadForm


def health(request):  # noqa: ARG001
    return JsonResponse({"status": "ok"})


def event_catalog(request):
    today = date.today()
    published = Event.objects.published()
    upcoming = list(published.filter(end_date__gte=today).order_by("start_date", "name"))
    past = list(published.filter(end_date__lt=today).order_by("-start_date", "name"))
    return render(request, "catalog/event_catalog.html", {"events": [*upcoming, *past]})


def event_detail(request, slug: str, *, selfie_search_form=None):
    event = get_object_or_404(Event.objects.published(), slug=slug)
    if selfie_search_form is None and settings.SELFIE_SEARCH_ENABLED:
        selfie_search_form = SelfieSearchUploadForm()
    gallery_photos: tuple[GalleryPhoto, ...] = ()
    gallery_page_data = None
    if event.access_type == Event.AccessType.FREE:
        try:
            gallery_page_data = gallery_page(event=event, page_number=request.GET.get("page"))
        except InvalidPage:
            return HttpResponse(status=404)
        gallery_photos = tuple(
            GalleryPhotoFactory.from_photo(photo=photo, event_slug=event.slug)
            for photo in gallery_page_data.object_list
        )
    return render(
        request,
        "catalog/event_detail.html",
        {
            "event": event,
            "gallery_photos": gallery_photos,
            "gallery_page": gallery_page_data,
            "selfie_search_form": selfie_search_form,
        },
    )


def _public_media_resolver() -> PublicMediaResolver:
    try:
        storage = PrivateUploadStorage()
    except ValueError:
        raise StorageUnavailable() from None
    return PublicMediaResolver(storage=storage)


@require_GET
def photo_media(request, slug: str, photo_id: str, variant: str) -> HttpResponse:  # noqa: ARG001
    if variant not in GALLERY_VARIANTS:
        return HttpResponse(status=404)
    event = get_object_or_404(
        Event.objects.published(), slug=slug, access_type=Event.AccessType.FREE
    )
    photo = get_object_or_404(gallery_photo_queryset(event=event), pk=photo_id)
    try:
        signed_url = _public_media_resolver().resolve_signed(photo=photo, variant=variant)
    except ObjectMissing:
        return HttpResponse(status=404)
    except StorageError:
        return HttpResponse(status=503)
    return redirect(signed_url)


@require_GET
def photo_download(request, slug: str, photo_id: str) -> HttpResponse:  # noqa: ARG001
    event = get_object_or_404(
        Event.objects.published(), slug=slug, access_type=Event.AccessType.FREE
    )
    photo = get_object_or_404(gallery_photo_queryset(event=event), pk=photo_id)
    try:
        signed_url = _public_media_resolver().resolve_download(photo=photo)
    except ObjectMissing:
        return HttpResponse(status=404)
    except StorageError:
        return HttpResponse(status=503)
    return redirect(signed_url)


def legacy_events_redirect(request):  # noqa: ARG001
    return redirect("event_catalog")


def legal(request):
    return render(request, "ui/legal.html")
