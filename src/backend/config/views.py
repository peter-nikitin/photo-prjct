from dataclasses import replace
from datetime import date
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import InvalidPage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET
from ingestion.storage import (
    ObjectMissing,
    PrivateUploadStorage,
    StorageError,
    StorageUnavailable,
)
from picflow.access import mark_event_staff_preview
from picflow.forms import EventGalleryFolderFilterForm, EventGalleryTimeFilterForm
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhoto,
    GalleryPhotoFactory,
    PublicMediaResolver,
    gallery_folder_choices,
    gallery_page,
    gallery_photo_queryset,
)
from picflow.models import Event, EventFolder, Photo
from prometheus_client import CONTENT_TYPE_LATEST
from selfie_search.forms import SelfieSearchUploadForm
from selfie_search.services.submission import gallery_search_faces_by_photo

from config.metrics import generate_metrics


def health(request):  # noqa: ARG001
    return JsonResponse({"status": "ok"})


@require_GET
def metrics(request):  # noqa: ARG001
    return HttpResponse(generate_metrics(), content_type=CONTENT_TYPE_LATEST)


def event_catalog(request):
    today = date.today()
    visible = Event.objects.site_visible_to(request.user)
    upcoming = list(visible.filter(end_date__gte=today).order_by("start_date", "name"))
    past = list(visible.filter(end_date__lt=today).order_by("-start_date", "name"))
    events = [*upcoming, *past]
    mark_event_staff_preview(request, events)
    return render(request, "catalog/event_catalog.html", {"events": events})


def event_detail(request, slug: str, *, selfie_search_form=None):
    event = get_object_or_404(Event.objects.site_visible_to(request.user), slug=slug)
    mark_event_staff_preview(request, (event,))
    if selfie_search_form is None and event.access_type == Event.AccessType.FREE:
        selfie_search_form = SelfieSearchUploadForm()
    selfie_feedback_enabled = bool(settings.SELFIE_FEEDBACK_ENABLED)
    gallery_photos: tuple[GalleryPhoto, ...] = ()
    gallery_page_data = None
    manual_time_filter_form = None
    manual_time_filter_invalid = False
    gallery_folder_choices_data: tuple[EventFolder, ...] = ()
    gallery_folder_filter_form = None
    gallery_pagination_query = ""
    gallery_pagination_query_pairs: tuple[tuple[str, str], ...] = ()
    gallery_filters_active = False
    if event.access_type == Event.AccessType.FREE:
        base_gallery_queryset = gallery_photo_queryset(event=event)
        gallery_folder_choices_data, has_unfiled = gallery_folder_choices(
            event=event, base_queryset=base_gallery_queryset
        )
        gallery_folder_filter_form = EventGalleryFolderFilterForm(
            event,
            gallery_folder_choices_data,
            request.GET,
            include_unfiled=bool(gallery_folder_choices_data) and has_unfiled,
        )
        gallery_folder_filter_form.is_valid()
        manual_time_filter_form = EventGalleryTimeFilterForm(event, request.GET)
        if manual_time_filter_form.is_requested and not manual_time_filter_form.is_valid():
            manual_time_filter_invalid = True
        else:
            bounds = manual_time_filter_form.utc_bounds
            gallery_filters_active = (
                manual_time_filter_form.is_requested or gallery_folder_filter_form.is_requested
            )
            query_pairs = [
                ("folder", str(folder_id))
                for folder_id in gallery_folder_filter_form.selected_folder_ids
            ]
            if gallery_folder_filter_form.include_unfiled:
                query_pairs.append(("unfiled", "1"))
            if manual_time_filter_form.is_requested:
                if manual_time_filter_form.cleaned_data["from"]:
                    query_pairs.append(("from", manual_time_filter_form.cleaned_data["from"]))
                if manual_time_filter_form.cleaned_data["to"]:
                    query_pairs.append(("to", manual_time_filter_form.cleaned_data["to"]))
            gallery_pagination_query_pairs = tuple(query_pairs)
            gallery_pagination_query = urlencode(gallery_pagination_query_pairs)
            try:
                gallery_page_data = gallery_page(
                    event=event,
                    page_number=request.GET.get("page"),
                    capture_time_start=bounds[0] if bounds else None,
                    capture_time_end=bounds[1] if bounds else None,
                    folder_ids=gallery_folder_filter_form.selected_folder_ids,
                    include_unfiled=gallery_folder_filter_form.include_unfiled,
                )
            except InvalidPage:
                return HttpResponse(status=404)
            gallery_page_photos = tuple(gallery_page_data.object_list)
            faces_by_photo = gallery_search_faces_by_photo(event=event, photos=gallery_page_photos)

            def faces(photo: Photo):
                return tuple(
                    replace(
                        face,
                        search_url=reverse(
                            "selfie_search:submit_gallery_face",
                            kwargs={
                                "event_slug": event.slug,
                                "photo_id": photo.pk,
                                "detection_id": face.detection_id,
                            },
                        ),
                    )
                    for face in faces_by_photo.get(photo.pk, ())
                )

            gallery_photos = tuple(
                GalleryPhotoFactory.from_photo(
                    photo=photo,
                    event_slug=event.slug,
                    faces=faces(photo),
                )
                for photo in gallery_page_photos
            )
    return render(
        request,
        "catalog/event_detail.html",
        {
            "event": event,
            "gallery_photos": gallery_photos,
            "gallery_page": gallery_page_data,
            "manual_time_filter_form": manual_time_filter_form,
            "manual_time_filter_invalid": manual_time_filter_invalid,
            "gallery_folder_choices": gallery_folder_choices_data,
            "gallery_folder_filter_form": gallery_folder_filter_form,
            "gallery_pagination_query": gallery_pagination_query,
            "gallery_pagination_query_pairs": gallery_pagination_query_pairs,
            "gallery_filters_active": gallery_filters_active,
            "selfie_search_form": selfie_search_form,
            "selfie_feedback_enabled": selfie_feedback_enabled,
        },
    )


def _public_media_resolver() -> PublicMediaResolver:
    try:
        storage = PrivateUploadStorage()
    except ValueError:
        raise StorageUnavailable() from None
    return PublicMediaResolver(storage=storage)


@require_GET
def photo_media(request, slug: str, photo_id: str, variant: str) -> HttpResponse:
    if variant not in GALLERY_VARIANTS:
        return HttpResponse(status=404)
    event = get_object_or_404(
        Event.objects.site_visible_to(request.user),
        slug=slug,
        access_type=Event.AccessType.FREE,
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
def photo_download(request, slug: str, photo_id: str) -> HttpResponse:
    event = get_object_or_404(
        Event.objects.site_visible_to(request.user),
        slug=slug,
        access_type=Event.AccessType.FREE,
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
