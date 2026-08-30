import logging
import re
from functools import wraps
from time import monotonic
from typing import Literal, cast
from urllib.parse import urlencode

from commerce.views import (
    apply_read_cookie_decision,
    cart_state_for_photos,
    private_cart_response,
)
from config.views import _paid_watermarked_previews_enabled, _public_media_resolver
from django import forms
from django.conf import settings
from django.core.paginator import InvalidPage
from django.http import (
    HttpResponse,
    HttpResponseBase,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST
from feature_flags import services as feature_flag_services
from feature_flags.registry import BULK_PHOTO_DOWNLOAD
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageError, StorageUnavailable
from picflow.access import mark_event_staff_preview
from picflow.archive import (
    ArchiveEntry,
    ArchiveSourceMissing,
    ArchiveSourceUnavailable,
    prepare_zip_archive,
)
from picflow.archive_presentation import archive_page_action
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhotoFactory,
    GalleryVariant,
    gallery_photo_queryset,
)
from picflow.models import Event, Photo

from selfie_search.forms import FeedbackSubmissionForm, SelfieSearchUploadForm
from selfie_search.models import SelfieSearch, SelfieSearchFeedback
from selfie_search.observability import SelfieEventName, emit_selfie_event
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
from selfie_search.services.submission import (
    GallerySearchFailed,
    GallerySearchUnavailable,
    process_gallery_photo_search,
    submit_gallery_photo_search,
    submit_selfie_search,
)
from selfie_search.storage import FeedbackSelfieStorage, TemporarySelfieStorage

_FEEDBACK_CORRELATION_RE = re.compile(r"\A[A-Za-z0-9_-]{32,64}\Z")


def _validated_feedback_correlation(value: str) -> str:
    return value if _FEEDBACK_CORRELATION_RE.fullmatch(value) else ""


logger = logging.getLogger(__name__)


@require_POST
def submit_gallery_face(request, event_slug: str, photo_id: str, detection_id):  # noqa: ARG001
    event = get_object_or_404(Event.objects.site_visible_to(request.user), slug=event_slug)
    paid_watermarked_previews_enabled = _paid_watermarked_previews_enabled(request)
    photo = get_object_or_404(
        gallery_photo_queryset(
            event=event,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        ),
        pk=photo_id,
    )
    try:
        created = submit_gallery_photo_search(
            event=event,
            photo=photo,
            detection_id=detection_id,
            user=request.user,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        )
    except GallerySearchUnavailable:
        return _not_found_response()
    except GallerySearchFailed:
        return HttpResponse(status=503)
    return redirect(
        "selfie_search:result",
        event_slug=event.slug,
        public_token=created.public_token,
    )


@require_POST
def submit(request, event_slug: str):
    started_at = monotonic()
    event = get_object_or_404(Event.objects.site_visible_to(request.user), slug=event_slug)
    feedback_correlation = (
        _validated_feedback_correlation(request.POST.get("feedback_correlation", ""))
        if settings.SELFIE_FEEDBACK_ENABLED
        else ""
    )
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
            user=request.user,
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
    result_url = reverse(
        "selfie_search:result",
        kwargs={"event_slug": event.slug, "public_token": created.public_token},
    )
    if feedback_correlation:
        result_url = f"{result_url}?{urlencode({'feedback_correlation': feedback_correlation})}"
    return redirect(result_url)


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


def _sanitize_result_exception_path(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Exception:
            sanitized_path = "/events/<event>/selfie-search/<bearer>/"
            request.path_info = sanitized_path
            request.META["PATH_INFO"] = sanitized_path
            raise

    return wrapper


@sensitive_variables()
@_sanitize_result_exception_path
def result(request, event_slug: str, public_token: str) -> HttpResponse:  # noqa: ARG001
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    paid_watermarked_previews_enabled = _paid_watermarked_previews_enabled(request)
    is_gallery_origin = search.configuration.get("processor") == "gallery_photo_query"
    selfie_search_page = None
    if search.status == SelfieSearch.Status.READY:
        try:
            selfie_search_page = saved_ready_result_page(
                search=search,
                page_number=request.GET.get("page"),
                paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
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
    cart_state = cart_state_for_photos(
        request=request,
        event=search.event,
        photos=gallery_photos,
        watermarked_previews_enabled=paid_watermarked_previews_enabled,
        require_eligible_photo=True,
    )
    feedback_context = None
    feedback_submitted = False
    feedback_correlation = (
        _validated_feedback_correlation(request.GET.get("feedback_correlation", ""))
        if settings.SELFIE_FEEDBACK_ENABLED
        else ""
    )
    if (
        settings.SELFIE_FEEDBACK_ENABLED
        and not is_gallery_origin
        and search.status in _TERMINAL_SEARCH_STATUSES
    ):
        feedback_submitted = SelfieSearchFeedback.objects.filter(search=search).exists()
        if not feedback_submitted:
            try:
                presentation = feedback_presentation(
                    search,
                    paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
                )
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
    archive_action = _result_archive_action(request=request, search=search, page=selfie_search_page)
    response = render(
        request,
        "selfie_search/result.html",
        {
            "cart_presentation": cart_state.presentation if cart_state is not None else None,
            "event": search.event,
            "archive_action": archive_action,
            "archive_url": (
                _result_archive_url(
                    search=search,
                    public_token=public_token,
                    page=selfie_search_page,
                )
                if archive_action is not None
                else None
            ),
            "gallery_photos": gallery_photos,
            "gallery_result_items": gallery_result_items,
            "feedback": feedback_context,
            "feedback_submitted": feedback_submitted,
            "selfie_feedback_enabled": bool(
                settings.SELFIE_FEEDBACK_ENABLED and not is_gallery_origin
            ),
            "is_gallery_origin": is_gallery_origin,
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
    if cart_state is not None:
        private_cart_response(response)
        apply_read_cookie_decision(
            response,
            delete_browser_token=cart_state.delete_browser_token,
        )
    return response


@require_POST
def process_gallery_search(request, event_slug: str, public_token: str) -> HttpResponse:  # noqa: ARG001
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if (
        search is None
        or search.status != SelfieSearch.Status.QUEUED
        or search.configuration.get("processor") != "gallery_photo_query"
    ):
        return _not_found_response()
    try:
        process_gallery_photo_search(
            search=search,
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        )
    except GallerySearchUnavailable:
        return _not_found_response()
    except GallerySearchFailed:
        return HttpResponse(status=503)
    return redirect(
        "selfie_search:result",
        event_slug=search.event.slug,
        public_token=public_token,
    )


def status(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    return JsonResponse(public_status_payload(search))


@require_POST
def feedback(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    if not settings.SELFIE_FEEDBACK_ENABLED:
        return _not_found_response()
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None or search.configuration.get("processor") == "gallery_photo_query":
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
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
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
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    photo = saved_ready_result_photo(
        search=search,
        photo_id=photo_id,
        paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
    )
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
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    photo = saved_ready_result_photo(
        search=search,
        photo_id=photo_id,
        paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
    )
    if photo is None:
        return _not_found_response()
    if photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED:
        return _not_found_response()
    try:
        signed_url = _public_media_resolver().resolve_download(photo=photo)
    except ObjectMissing:
        return _not_found_response()
    except StorageError:
        return HttpResponse(status=503)
    return redirect(signed_url)


@require_GET
def result_archive(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    if not feature_flag_services.is_enabled(BULK_PHOTO_DOWNLOAD, request.user):
        return _not_found_response()
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None or search.event.access_type != Event.AccessType.FREE:
        return _not_found_response()
    try:
        page = saved_ready_result_page(
            search=search,
            page_number=request.GET.get("page"),
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        )
    except InvalidPage:
        return _not_found_response()
    entries = _archive_entries(page)
    if entries is None or len(entries) < 2:
        return _not_found_response()
    try:
        archive = prepare_zip_archive(entries=entries, storage=_archive_storage())
    except ArchiveSourceMissing:
        return _not_found_response()
    except (ArchiveSourceUnavailable, StorageUnavailable):
        return HttpResponse(status=503)
    response = StreamingHttpResponse(archive, content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="{_result_archive_filename(search=search, page=page)}"'
    )
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Accel-Buffering"] = "no"
    return response


def _event_page(request, event_slug: str, form: SelfieSearchUploadForm, *, status: int = 200):
    from config.views import event_detail

    response = event_detail(request, event_slug, selfie_search_form=form)
    response.status_code = status
    return response


def _result_archive_action(*, request, search: SelfieSearch, page):
    if (
        page is None
        or search.event.access_type != Event.AccessType.FREE
        or not feature_flag_services.is_enabled(BULK_PHOTO_DOWNLOAD, request.user)
    ):
        return None
    return archive_page_action(
        item_count=len(page.object_list),
        page_number=page.number,
        page_count=page.paginator.num_pages,
    )


def _result_archive_url(*, search: SelfieSearch, public_token: str, page) -> str:
    url = reverse(
        "selfie_search:result_archive",
        kwargs={"event_slug": search.event.slug, "public_token": public_token},
    )
    if page.paginator.num_pages == 1:
        return url
    return f"{url}?{urlencode({'page': page.number})}"


def _archive_entries(page) -> tuple[ArchiveEntry, ...] | None:
    entries: list[ArchiveEntry] = []
    for row in page.object_list:
        photo = row.photo
        if (
            not isinstance(photo.original_key, str)
            or not photo.original_key
            or isinstance(photo.original_size, bool)
            or not isinstance(photo.original_size, int)
            or photo.original_size < 0
            or photo.original_content_type not in {"image/jpeg", "image/png"}
        ):
            return None
        entries.append(
            ArchiveEntry(
                photo_id=photo.pk,
                original_key=photo.original_key,
                original_size=photo.original_size,
                original_content_type=cast(
                    Literal["image/jpeg", "image/png"], photo.original_content_type
                ),
            )
        )
    return tuple(entries)


def _archive_storage() -> PrivateUploadStorage:
    try:
        return PrivateUploadStorage()
    except (TypeError, ValueError):
        raise StorageUnavailable() from None


def _result_archive_filename(*, search: SelfieSearch, page) -> str:
    if page.paginator.num_pages == 1:
        return f"findme-photo-{search.event.slug}-search-results.zip"
    return f"findme-photo-{search.event.slug}-search-page-{page.number}.zip"


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


def _public_search(request, *, event_slug: str, public_token: str) -> SelfieSearch | None:
    try:
        search = resolve_public_result(
            event_slug=event_slug,
            public_token=public_token,
            user=request.user,
        )
    except PublicSearchNotFound:
        return None
    mark_event_staff_preview(request, (search.event,))
    return search


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
