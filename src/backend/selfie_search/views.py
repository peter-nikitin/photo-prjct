import re
from urllib.parse import urlencode

from config.views import _public_media_resolver
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

from selfie_search.forms import FeedbackSubmissionForm, SelfieSearchUploadForm
from selfie_search.models import SelfieSearch, SelfieSearchFeedback
from selfie_search.services.feedback import (
    FeedbackInvalid,
    FeedbackNonTerminal,
    FeedbackResultChanged,
    feedback_presentation,
    submit_search_feedback,
)
from selfie_search.services.results import (
    PublicSearchNotFound,
    public_status_payload,
    resolve_public_result,
    saved_ready_result_page,
    saved_ready_result_photo,
)
from selfie_search.services.submission import submit_selfie_search
from selfie_search.storage import FeedbackSelfieStorage, TemporarySelfieStorage

_FEEDBACK_CORRELATION_RE = re.compile(r"\A[A-Za-z0-9_-]{32,64}\Z")


def _validated_feedback_correlation(value: str) -> str:
    return value if _FEEDBACK_CORRELATION_RE.fullmatch(value) else ""


@require_POST
def submit(request, event_slug: str):
    event = get_object_or_404(Event.objects.published(), slug=event_slug)
    if not settings.SELFIE_SEARCH_ENABLED:
        raise Http404
    feedback_correlation = (
        _validated_feedback_correlation(request.POST.get("feedback_correlation", ""))
        if settings.SELFIE_FEEDBACK_ENABLED
        else ""
    )
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
    result_url = reverse(
        "selfie_search:result",
        kwargs={"event_slug": event.slug, "public_token": created.public_token},
    )
    if feedback_correlation:
        result_url = f"{result_url}?{urlencode({'feedback_correlation': feedback_correlation})}"
    return redirect(result_url)


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
    gallery_result_items = (
        tuple(zip(selfie_search_page.object_list, gallery_photos, strict=True))
        if selfie_search_page is not None
        else ()
    )
    feedback_context = None
    feedback_submitted = False
    feedback_correlation = (
        _validated_feedback_correlation(request.GET.get("feedback_correlation", ""))
        if settings.SELFIE_FEEDBACK_ENABLED
        else ""
    )
    if settings.SELFIE_FEEDBACK_ENABLED and search.status in _TERMINAL_SEARCH_STATUSES:
        feedback_submitted = SelfieSearchFeedback.objects.filter(search=search).exists()
        if not feedback_submitted:
            try:
                presentation = feedback_presentation(search)
            except FeedbackNonTerminal:
                presentation = None
            if presentation is not None:
                feedback_context = {
                    "variant": presentation.variant,
                    "visible_result_count": presentation.visible_result_count,
                    "url": reverse(
                        "selfie_search:feedback",
                        kwargs={"event_slug": search.event.slug, "public_token": public_token},
                    ),
                }
    response = render(
        request,
        "selfie_search/result.html",
        {
            "event": search.event,
            "gallery_photos": gallery_photos,
            "gallery_result_items": gallery_result_items,
            "feedback": feedback_context,
            "feedback_submitted": feedback_submitted,
            "selfie_feedback_enabled": bool(settings.SELFIE_FEEDBACK_ENABLED),
            "selfie_feedback_correlation": feedback_correlation,
            "selfie_search_page": selfie_search_page,
            "is_terminal": search.status in _TERMINAL_SEARCH_STATUSES,
            "public_token": public_token,
            "public_token_digest": search.public_token_digest,
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


@require_POST
def feedback(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    if not settings.SELFIE_FEEDBACK_ENABLED:
        return _not_found_response()
    search = _public_search(event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    form = FeedbackSubmissionForm(data=request.POST, files=request.FILES)
    if not form.is_valid():
        return JsonResponse({"status": "invalid"}, status=422)
    try:
        submission = submit_search_feedback(
            search_id=search.pk,
            upload=form.cleaned_data["selfie"],
            contact=form.cleaned_data["contact"],
            labels=form.cleaned_data["labels"],
            storage=FeedbackSelfieStorage(),
        )
    except FeedbackInvalid:
        return JsonResponse({"status": "invalid"}, status=422)
    except FeedbackNonTerminal:
        return JsonResponse({"status": "non_terminal"}, status=409)
    except FeedbackResultChanged:
        return JsonResponse({"status": "result_changed"}, status=409)
    except StorageUnavailable:
        return JsonResponse({"status": "storage_unavailable"}, status=503)
    status = 201 if submission.created else 200
    outcome = "submitted" if submission.created else "already_submitted"
    return JsonResponse({"status": outcome}, status=status)


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
