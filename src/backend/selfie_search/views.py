from config.views import _public_media_resolver
from django.conf import settings
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBase,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from ingestion.storage import ObjectMissing, StorageError, StorageUnavailable
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhotoFactory,
    GalleryVariant,
)
from picflow.models import Event, Photo
from picflow.pagination import InvalidCursor

from selfie_search.forms import SelfieSearchUploadForm
from selfie_search.models import SelfieSearch
from selfie_search.services.results import (
    PublicSearchNotFound,
    public_status_payload,
    resolve_public_result,
    saved_ready_result_page,
    saved_ready_result_photo,
)
from selfie_search.services.submission import submit_selfie_search
from selfie_search.storage import TemporarySelfieStorage


@require_POST
def submit(request, event_slug: str):
    event = get_object_or_404(Event.objects.published(), slug=event_slug)
    if not settings.SELFIE_SEARCH_ENABLED:
        raise Http404
    form = SelfieSearchUploadForm(files=request.FILES)
    if not form.is_valid():
        return _event_page(request, event_slug, form)
    try:
        created = submit_selfie_search(
            event=event,
            upload=form.cleaned_data["selfie"],
            storage=TemporarySelfieStorage(),
        )
    except StorageUnavailable:
        form.add_error("selfie", "Не удалось загрузить селфи. Попробуйте ещё раз.")
        return _event_page(request, event_slug, form)
    return redirect(
        "selfie_search:result",
        event_slug=event.slug,
        public_token=created.public_token,
    )


def result(request, event_slug: str, public_token: str) -> HttpResponse:  # noqa: ARG001
    search = _public_search(event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    selfie_search_next_cursor: str | None = None
    if search.status == SelfieSearch.Status.READY:
        try:
            page = saved_ready_result_page(search=search, cursor=request.GET.get("cursor"))
        except InvalidCursor:
            return _not_found_response()
        photos = page.photos
        selfie_search_next_cursor = page.next_cursor
    else:
        photos = ()
    gallery_photos = tuple(
        GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=search.event.slug,
            media_url_builder=_result_media_url_builder(
                event_slug=search.event.slug,
                public_token=public_token,
            ),
        )
        for photo in photos
    )
    response = render(
        request,
        "selfie_search/result.html",
        {
            "event": search.event,
            "gallery_photos": gallery_photos,
            "selfie_search_next_cursor": selfie_search_next_cursor,
            "is_terminal": search.status in _TERMINAL_SEARCH_STATUSES,
            "public_token": public_token,
            "search": search,
            "status_url": reverse(
                "selfie_search:status",
                kwargs={"event_slug": search.event.slug, "public_token": public_token},
            ),
        },
    )
    return response


def status(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    search = _public_search(event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    return JsonResponse(public_status_payload(search))


def result_media(
    request, event_slug: str, public_token: str, photo_id: str, variant: str
) -> HttpResponseBase:  # noqa: ARG001
    if variant not in GALLERY_VARIANTS:
        return _not_found_response()
    search = _public_search(event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    photo = saved_ready_result_photo(search=search, photo_id=photo_id)
    if photo is None:
        return _not_found_response()
    try:
        signed_url = _public_media_resolver().resolve_signed(photo=photo, variant=variant)
    except ObjectMissing:
        return _not_found_response()
    except StorageError:
        return HttpResponse(status=503)
    return redirect(signed_url)


def _event_page(request, event_slug: str, form: SelfieSearchUploadForm):
    from config.views import event_detail

    return event_detail(request, event_slug, selfie_search_form=form)


_TERMINAL_SEARCH_STATUSES = frozenset(
    {
        SelfieSearch.Status.READY,
        SelfieSearch.Status.NO_FACE,
        SelfieSearch.Status.MULTIPLE_FACES,
        SelfieSearch.Status.QUALITY_REJECTED,
        SelfieSearch.Status.SEARCH_UNAVAILABLE,
        SelfieSearch.Status.FAILED,
    }
)


def _public_search(*, event_slug: str, public_token: str) -> SelfieSearch | None:
    try:
        return resolve_public_result(event_slug=event_slug, public_token=public_token)
    except PublicSearchNotFound:
        return None


def _result_media_url_builder(*, event_slug: str, public_token: str):
    def build(photo: Photo, variant: GalleryVariant) -> str:
        return reverse(
            "selfie_search:result_media",
            kwargs={
                "event_slug": event_slug,
                "public_token": public_token,
                "photo_id": photo.pk,
                "variant": variant,
            },
        )

    return build


def _not_found_response() -> HttpResponse:
    return HttpResponse(status=404)
