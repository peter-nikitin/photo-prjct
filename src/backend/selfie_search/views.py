import logging
from time import monotonic

from config.views import _public_media_resolver
from django import forms
from django.conf import settings
from django.core.paginator import InvalidPage
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBase,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from ingestion.storage import ObjectMissing, StorageError, StorageUnavailable
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhotoFactory,
    GalleryVariant,
)
from picflow.models import Event, Photo

from selfie_search.forms import SelfieSearchUploadForm
from selfie_search.models import SelfieSearch
from selfie_search.observability import SelfieEventName, emit_selfie_event
from selfie_search.services.results import (
    PublicSearchNotFound,
    public_status_payload,
    resolve_public_result,
    saved_ready_result_page,
    saved_ready_result_photo,
)
from selfie_search.services.submission import submit_selfie_search
from selfie_search.storage import TemporarySelfieStorage

logger = logging.getLogger(__name__)


@require_POST
def submit(request, event_slug: str):
    started_at = monotonic()
    event = get_object_or_404(Event.objects.published(), slug=event_slug)
    if not settings.SELFIE_SEARCH_ENABLED:
        raise Http404
    form = SelfieSearchUploadForm(files=request.FILES)
    if not form.is_valid():
        reason_code = form.image_rejection.reason if form.image_rejection else "missing_or_empty"
        _emit_submission_finished(
            event=event,
            form=form,
            outcome="rejected",
            reason_code=reason_code,
            search_id=None,
            started_at=started_at,
            level=logging.INFO,
        )
        return _event_page(request, event_slug, form, status=422)
    selfie = form.cleaned_data["selfie"]
    try:
        created = submit_selfie_search(
            event=event,
            selfie=selfie,
            storage=TemporarySelfieStorage(),
        )
    except StorageUnavailable:
        _emit_submission_finished(
            event=event,
            form=form,
            outcome="storage_unavailable",
            reason_code="storage_unavailable",
            search_id=None,
            started_at=started_at,
            level=logging.WARNING,
        )
        form.add_error(
            None,
            forms.ValidationError(
                "Не удалось загрузить фотографию. Попробуйте ещё раз.",
                code="storage_unavailable",
            ),
        )
        return _event_page(request, event_slug, form, status=503)
    _emit_submission_finished(
        event=event,
        form=form,
        outcome="accepted",
        reason_code="",
        search_id=created.search.pk,
        started_at=started_at,
        level=logging.INFO,
    )
    return redirect(
        "selfie_search:result",
        event_slug=event.slug,
        public_token=created.public_token,
    )


def _emit_submission_finished(
    *,
    event: Event,
    form: SelfieSearchUploadForm,
    outcome: str,
    reason_code: str,
    search_id: object | None,
    started_at: float,
    level: int,
) -> None:
    observation = form.observation()
    emit_selfie_event(
        logger,
        event=SelfieEventName.SUBMISSION_FINISHED,
        level=level,
        event_id=event.pk,
        outcome=outcome,
        reason_code=reason_code,
        search_id=search_id,
        actual_format=observation.actual_format,
        declared_type=observation.declared_type,
        source_size_bucket=observation.source_size_bucket,
        duration_ms=int((monotonic() - started_at) * 1_000),
    )


def result(request, event_slug: str, public_token: str) -> HttpResponse:  # noqa: ARG001
    search = _public_search(event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    selfie_search_page = None
    if search.status == SelfieSearch.Status.READY:
        try:
            selfie_search_page = saved_ready_result_page(
                search=search, page_number=request.GET.get("page")
            )
        except InvalidPage:
            return _not_found_response()
        photos = tuple(row.photo for row in selfie_search_page.object_list)
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
            download_url_builder=_result_download_url_builder(
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
            "selfie_search_page": selfie_search_page,
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


@require_GET
def result_download(request, event_slug: str, public_token: str, photo_id: str) -> HttpResponseBase:  # noqa: ARG001
    search = _public_search(event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    photo = saved_ready_result_photo(search=search, photo_id=photo_id)
    if photo is None:
        return _not_found_response()
    try:
        signed_url = _public_media_resolver().resolve_download(photo=photo)
    except ObjectMissing:
        return _not_found_response()
    except StorageError:
        return HttpResponse(status=503)
    return redirect(signed_url)


def _event_page(request, event_slug: str, form: SelfieSearchUploadForm, *, status: int = 200):
    from config.views import event_detail

    response = event_detail(request, event_slug, selfie_search_form=form)
    response.status_code = status
    return response


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


def _result_download_url_builder(*, event_slug: str, public_token: str):
    def build(photo: Photo) -> str:
        return reverse(
            "selfie_search:result_download",
            kwargs={
                "event_slug": event_slug,
                "public_token": public_token,
                "photo_id": photo.pk,
            },
        )

    return build


def _not_found_response() -> HttpResponse:
    return HttpResponse(status=404)
