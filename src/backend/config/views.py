from dataclasses import replace
from datetime import date
from urllib.parse import urlencode

from commerce.views import (
    apply_read_cookie_decision,
    cart_state_for_photos,
    private_cart_response,
)
from django.conf import settings
from django.core.paginator import InvalidPage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET
from feature_flags import services as feature_flag_services
from feature_flags.registry import PAID_WATERMARKED_PREVIEWS
from ingestion.storage import (
    ObjectMissing,
    PrivateUploadStorage,
    StorageError,
    StorageUnavailable,
)
from picflow.access import mark_event_staff_preview
from picflow.forms import BibSearchForm, EventGalleryFolderFilterForm, EventGalleryTimeFilterForm
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhoto,
    GalleryPhotoFactory,
    MediaUrlBuilder,
    PublicMediaResolver,
    gallery_folder_choices,
    gallery_page,
    gallery_photo_queryset,
)
from picflow.gallery_preview_grants import issue_gallery_preview_urls
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


@sensitive_variables()
def event_detail(request, slug: str, *, selfie_search_form=None):
    event = get_object_or_404(Event.objects.site_visible_to(request.user), slug=slug)
    mark_event_staff_preview(request, (event,))
    if selfie_search_form is None:
        selfie_search_form = SelfieSearchUploadForm()
    selfie_feedback_enabled = bool(settings.SELFIE_FEEDBACK_ENABLED)
    gallery_photos: tuple[GalleryPhoto, ...] = ()
    gallery_page_data = None
    bib_search_form = None
    bib_search_invalid = False
    bib_number = None
    if event.bib_search_enabled:
        bib_search_form = BibSearchForm(request.GET if "bib" in request.GET else None)
        if bib_search_form.is_bound:
            if bib_search_form.is_valid():
                bib_number = bib_search_form.cleaned_data["bib"] or None
            else:
                bib_search_invalid = True
    manual_time_filter_form = None
    manual_time_filter_invalid = False
    gallery_folder_choices_data: tuple[EventFolder, ...] = ()
    gallery_folder_filter_form = None
    gallery_pagination_query = ""
    gallery_pagination_query_pairs: tuple[tuple[str, str], ...] = ()
    gallery_filters_active = False
    paid_watermarked_previews_enabled = _paid_watermarked_previews_enabled(request)
    if event.access_type == Event.AccessType.FREE or paid_watermarked_previews_enabled:
        base_gallery_queryset = gallery_photo_queryset(
            event=event,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        )
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
        elif not bib_search_invalid:
            bounds = manual_time_filter_form.utc_bounds
            gallery_filters_active = (
                manual_time_filter_form.is_requested
                or gallery_folder_filter_form.is_requested
                or bib_number is not None
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
            if bib_number is not None:
                query_pairs.append(("bib", bib_number))
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
                    bib_number=bib_number,
                    paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
                )
            except InvalidPage:
                return HttpResponse(status=404)
            gallery_page_photos = tuple(gallery_page_data.object_list)
            media_url_builder: MediaUrlBuilder | None = None
            if any(
                photo.gallery_media_policy != Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED
                for photo in gallery_page_photos
            ):
                try:
                    preview_urls = issue_gallery_preview_urls(
                        photos=gallery_page_photos,
                        signer=PrivateUploadStorage(),
                    )
                except (StorageError, ValueError):
                    return HttpResponse(status=503)

                def direct_preview_media_url(photo: Photo, variant: str) -> str:
                    if variant == "preview-small" and photo.pk in preview_urls:
                        return preview_urls[photo.pk]
                    return reverse(
                        "photo_media",
                        kwargs={"slug": event.slug, "photo_id": photo.pk, "variant": variant},
                    )

                media_url_builder = direct_preview_media_url
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
                    media_url_builder=media_url_builder,
                )
                for photo in gallery_page_photos
            )
    cart_state = cart_state_for_photos(
        request=request,
        event=event,
        photos=gallery_photos,
        watermarked_previews_enabled=paid_watermarked_previews_enabled,
    )
    response = render(
        request,
        "catalog/event_detail.html",
        {
            "cart_presentation": cart_state.presentation if cart_state is not None else None,
            "event": event,
            "bib_number": bib_number,
            "bib_search_form": bib_search_form,
            "bib_search_invalid": bib_search_invalid,
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
    if cart_state is not None:
        private_cart_response(response)
        apply_read_cookie_decision(
            response,
            delete_browser_token=cart_state.delete_browser_token,
        )
    return response


def _public_media_resolver() -> PublicMediaResolver:
    try:
        storage = PrivateUploadStorage()
    except ValueError:
        raise StorageUnavailable() from None
    return PublicMediaResolver(storage=storage)


def _paid_watermarked_previews_enabled(request) -> bool:
    return feature_flag_services.is_enabled(PAID_WATERMARKED_PREVIEWS, request.user)


@require_GET
def photo_media(request, slug: str, photo_id: str, variant: str) -> HttpResponse:
    if variant not in GALLERY_VARIANTS:
        return HttpResponse(status=404)
    event = get_object_or_404(
        Event.objects.site_visible_to(request.user),
        slug=slug,
    )
    photo = get_object_or_404(
        gallery_photo_queryset(
            event=event,
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        ),
        pk=photo_id,
    )
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
    )
    photo = get_object_or_404(
        gallery_photo_queryset(
            event=event,
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        ),
        pk=photo_id,
    )
    if photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED:
        return HttpResponse(status=404)
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
