import logging
import re
from functools import wraps
from time import monotonic
from typing import Literal, cast
from urllib.parse import urlencode

from commerce.views import (
    cart_state_for_photos,
    private_cart_response,
)
from config.views import _paid_watermarked_previews_enabled, _public_media_resolver
from django import forms
from django.conf import settings
from django.http import (
    HttpResponse,
    HttpResponseBase,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST
from feature_flags import services as feature_flag_services
from feature_flags.registry import BULK_PHOTO_DOWNLOAD
from ingestion.storage import ObjectMissing, StorageError, StorageUnavailable
from picflow.access import mark_event_staff_preview
from picflow.archive import ArchiveEntry, ArchiveObservation
from picflow.archive_delivery import prepare_archive_response
from picflow.archive_presentation import ArchivePageAction
from picflow.gallery import (
    GALLERY_VARIANTS,
    GalleryPhotoFactory,
    GalleryVariant,
    gallery_photo_queryset,
)
from picflow.models import Event, Photo

from selfie_search.forms import FeedbackSelfieForm, SelfieSearchUploadForm
from selfie_search.models import SelfieSearch, SelfieSearchFeedback
from selfie_search.observability import SelfieEventName, emit_selfie_event
from selfie_search.services.continuation import (
    ContinuationUnavailable,
    available_manual_photo_ids,
    submit_continuation,
)
from selfie_search.services.feedback import save_feedback_selfie
from selfie_search.services.negative_feedback import negative_feedback_state
from selfie_search.services.results import (
    PublicSearchNotFound,
    public_status_payload,
    resolve_public_result,
    saved_ready_result_photo,
    saved_ready_result_photos,
    saved_ready_result_rows,
)
from selfie_search.services.submission import (
    MAX_SELFIE_UPLOADS,
    AdditionalSelfieUnavailable,
    GallerySearchFailed,
    GallerySearchUnavailable,
    is_additional_selfie,
    selfie_upload_count,
    submit_additional_selfie,
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
    passes = search.continuation_passes.order_by("-created_at", "-pk")
    last_pass = passes.first()
    pending = last_pass is not None and last_pass.status in (
        SelfieSearch.Status.QUEUED,
        SelfieSearch.Status.PROCESSING,
    )
    is_terminal = search.status in _TERMINAL_SEARCH_STATUSES
    latest_ready = last_pass
    if last_pass is not None and last_pass.status != SelfieSearch.Status.READY:
        latest_ready = passes.filter(status=SelfieSearch.Status.READY).first()
    result_ready = search.status == SelfieSearch.Status.READY or latest_ready is not None
    last_is_selfie = last_pass is not None and is_additional_selfie(last_pass)
    display_status = (
        SelfieSearch.Status.READY
        if result_ready
        else last_pass.status
        if last_pass is not None and last_is_selfie
        else search.status
    )
    upload_count = selfie_upload_count(search) if is_terminal else 0
    is_gallery_origin = search.configuration.get("processor") == "gallery_photo_query"
    negative_feedback = negative_feedback_state(search)
    saved_rows = tuple(
        saved_ready_result_rows(
            search, paid_watermarked_previews_enabled=paid_watermarked_previews_enabled
        )
    )
    saved_photo_ids = {row.photo_id for row in saved_rows}
    excluded_ids = negative_feedback.excluded_photo_ids & saved_photo_ids
    reported_ids = negative_feedback.manual_photo_ids & saved_photo_ids
    result_rows = tuple(row for row in saved_rows if row.photo_id not in excluded_ids)
    photos = tuple(row.photo for row in result_rows)
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
    continuation_url = None
    usable_photo_ids: set[str] = set()
    if result_rows:
        continuation_url = reverse(
            "selfie_search:continue",
            kwargs={"event_slug": event_slug, "public_token": public_token},
        )
        if not pending:
            usable_photo_ids = available_manual_photo_ids(
                search, photo_ids=tuple(photo.pk for photo in photos)
            )
    cart_state = cart_state_for_photos(
        request=request,
        event=search.event,
        photos=gallery_photos,
        watermarked_previews_enabled=paid_watermarked_previews_enabled,
        require_eligible_photo=True,
    )
    cart_photos = (
        {item.photo.photo_id: item for item in cart_state.presentation.photos}
        if cart_state is not None
        else {}
    )
    gallery_result_items = tuple(
        (row, photo, cart_photos.get(photo.photo_id))
        for row, photo in zip(result_rows, gallery_photos, strict=True)
    )
    feedback_context = None
    feedback_submitted = False
    feedback_correlation = (
        _validated_feedback_correlation(request.GET.get("feedback_correlation", ""))
        if settings.SELFIE_FEEDBACK_ENABLED
        else ""
    )
    if settings.SELFIE_FEEDBACK_ENABLED and not is_gallery_origin and is_terminal:
        feedback_submitted = SelfieSearchFeedback.objects.filter(search=search).exists()
        if not feedback_submitted:
            feedback_context = {
                "is_problem": not any(row.search_id == search.pk for row in result_rows),
                "url": reverse(
                    "selfie_search:feedback_selfie",
                    kwargs={"event_slug": search.event.slug, "public_token": public_token},
                ),
            }
    published_count = len(result_rows)
    archive_action = (
        ArchivePageAction(label="Скачать все", helper_text=None)
        if published_count >= 2
        and search.event.access_type == Event.AccessType.FREE
        and feature_flag_services.is_enabled(BULK_PHOTO_DOWNLOAD, request.user)
        else None
    )
    continuation_empty = (
        last_pass is not None
        and last_pass.status == SelfieSearch.Status.READY
        and (
            last_pass.published_matched_photo_count == 0
            or (
                bool(excluded_ids)
                and not last_pass.results.filter(excluded_by_apparel=False)
                .exclude(photo_id__in=excluded_ids)
                .exists()
            )
        )
    )
    no_new_anchors = last_pass is not None and last_pass.failure_code == "continuation_unavailable"
    response = render(
        request,
        "selfie_search/result.html",
        {
            "event": search.event,
            "cart_presentation": cart_state.presentation if cart_state is not None else None,
            "archive_action": archive_action,
            "archive_url": reverse(
                "selfie_search:result_archive",
                kwargs={"event_slug": search.event.slug, "public_token": public_token},
            )
            if archive_action is not None
            else None,
            "published_count": published_count,
            "checked_photo_count": (latest_ready or search).eligible_photo_count,
            "all_results_marked": published_count == len(reported_ids),
            "feedback_excluded_photo_count": len(excluded_ids),
            "continuation_url": continuation_url,
            "continuation_pending": pending,
            "continuation_unavailable": request.GET.get("more") == "unavailable" or no_new_anchors,
            "continuation_empty": continuation_empty,
            "continuation_failed": last_pass is not None
            and not last_is_selfie
            and not no_new_anchors
            and last_pass.status
            in (
                SelfieSearch.Status.FAILED,
                SelfieSearch.Status.SEARCH_UNAVAILABLE,
            ),
            "continuation_photo_ids": usable_photo_ids,
            "gallery_photos": gallery_photos,
            "gallery_result_items": gallery_result_items,
            "feedback": feedback_context,
            "result_feedback_url": reverse(
                "search_feedback_submit", kwargs={"event_slug": search.event.slug}
            ),
            "reported_photo_ids": reported_ids,
            "similar_feedback_photo_ids": negative_feedback.similar_photo_ids,
            "feedback_submitted": feedback_submitted,
            "selfie_feedback_enabled": bool(
                settings.SELFIE_FEEDBACK_ENABLED and not is_gallery_origin
            ),
            "is_gallery_origin": is_gallery_origin,
            "selfie_feedback_correlation": feedback_correlation,
            "is_terminal": is_terminal,
            "display_status": display_status,
            "upload_count": upload_count,
            "upload_limit": MAX_SELFIE_UPLOADS,
            "selfie_upload_form": SelfieSearchUploadForm()
            if 0 < upload_count < MAX_SELFIE_UPLOADS and not pending
            else None,
            "selfie_pass_error": _SELFIE_PASS_ERRORS.get(last_pass.status)
            if last_pass is not None and last_is_selfie and result_ready
            else None,
            "public_token": public_token,
            "public_token_digest": search.public_token_digest,
            "search": search,
            "status_url": reverse(
                "selfie_search:status",
                kwargs={"event_slug": search.event.slug, "public_token": public_token},
            )
            + ("?continuation=1" if pending else ""),
        },
    )
    if cart_state is not None:
        private_cart_response(response)
    return response


@require_POST
@sensitive_post_parameters()
@_sanitize_result_exception_path
@sensitive_variables()
def add_selfie(request, event_slug: str, public_token: str) -> HttpResponseBase:
    started_at = monotonic()
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    try:
        expected_upload_count = int(request.POST.get("upload_count", ""))
    except ValueError:
        expected_upload_count = 0
    if not 1 <= expected_upload_count <= MAX_SELFIE_UPLOADS:
        return JsonResponse({"error": "Обновите страницу и повторите загрузку."}, status=422)
    if len(request.FILES.getlist("selfie")) > 1:
        return JsonResponse({"error": "Выберите одно селфи для этого прохода."}, status=422)
    form = SelfieSearchUploadForm(files=request.FILES)
    if not form.is_valid():
        return JsonResponse({"error": form.errors["selfie"][0]}, status=422)
    result_url = reverse(
        "selfie_search:result", kwargs={"event_slug": event_slug, "public_token": public_token}
    )
    try:
        child = submit_additional_selfie(
            search=search,
            selfie=form.cleaned_data["selfie"],
            storage=TemporarySelfieStorage(),
            user=request.user,
            expected_upload_count=expected_upload_count,
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        )
    except AdditionalSelfieUnavailable as error:
        if error.reason == "stale":
            return JsonResponse({"result_url": result_url}, status=202)
        if error.reason == "limit":
            return JsonResponse({"error": "В этом поиске уже использовано три селфи."}, status=409)
        if error.reason == "pending":
            return JsonResponse({"error": "Дождитесь завершения текущего прохода."}, status=409)
        return _not_found_response()
    except StorageUnavailable:
        return JsonResponse(
            {"error": "Не удалось загрузить фотографию. Попробуйте ещё раз."}, status=503
        )
    _emit_submission_finished(
        event=search.event,
        form=form,
        outcome="accepted",
        reason_code="",
        search_id=child.pk,
        started_at=started_at,
        level=logging.INFO,
    )
    return JsonResponse({"result_url": result_url}, status=202)


@require_POST
@_sanitize_result_exception_path
@sensitive_variables("public_token")
def continue_search(request, event_slug: str, public_token: str) -> HttpResponseBase:
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    photo_id = request.POST.get("photo_id")
    if photo_id is not None and not (1 <= len(photo_id) <= 32):
        return _not_found_response()
    target = reverse(
        "selfie_search:result", kwargs={"event_slug": event_slug, "public_token": public_token}
    )
    try:
        submit_continuation(
            search=search,
            user=request.user,
            photo_id=photo_id,
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        )
    except ContinuationUnavailable:
        target = f"{target}?more=unavailable"
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"result_url": target})
    return redirect(target)


def status(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None:
        return _not_found_response()
    if request.GET.get("continuation") == "1":
        last_pass = search.continuation_passes.order_by("-created_at", "-pk").first()
        if last_pass is not None:
            return JsonResponse(public_status_payload(last_pass))
    return JsonResponse(public_status_payload(search))


@require_POST
@sensitive_post_parameters()
@_sanitize_result_exception_path
@sensitive_variables()
def feedback_selfie(request, event_slug: str, public_token: str) -> HttpResponseBase:
    if not settings.SELFIE_FEEDBACK_ENABLED:
        return _not_found_response()
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None or search.configuration.get("processor") == "gallery_photo_query":
        return _not_found_response()
    if search.status not in _TERMINAL_SEARCH_STATUSES:
        return JsonResponse({"status": "non_terminal"}, status=409)
    form = FeedbackSelfieForm(data=request.POST, files=request.FILES)
    if not form.is_valid():
        return JsonResponse({"status": "invalid"}, status=422)
    try:
        created = save_feedback_selfie(
            search_id=search.pk,
            upload=form.cleaned_data["selfie"],
            contact=form.cleaned_data["contact"],
            storage=FeedbackSelfieStorage(),
            paid_watermarked_previews_enabled=_paid_watermarked_previews_enabled(request),
        )
    except StorageUnavailable:
        return JsonResponse({"status": "storage_unavailable"}, status=503)
    return JsonResponse(
        {"status": "submitted" if created else "already_submitted"},
        status=201 if created else 200,
    )


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
        signed_url = _public_media_resolver(request).resolve_signed(photo=photo, variant=variant)
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
        signed_url = _public_media_resolver(request).resolve_download(photo=photo)
    except ObjectMissing:
        return _not_found_response()
    except StorageError:
        return HttpResponse(status=503)
    return redirect(signed_url)


@require_POST
def result_archive(request, event_slug: str, public_token: str) -> HttpResponseBase:  # noqa: ARG001
    if not feature_flag_services.is_enabled(BULK_PHOTO_DOWNLOAD, request.user):
        return _not_found_response()
    search = _public_search(request, event_slug=event_slug, public_token=public_token)
    if search is None or search.event.access_type != Event.AccessType.FREE:
        return _not_found_response()
    paid_previews = _paid_watermarked_previews_enabled(request)
    feedback = negative_feedback_state(search)
    photos = tuple(
        photo
        for photo in saved_ready_result_photos(
            search, paid_watermarked_previews_enabled=paid_previews
        )
        if photo.pk not in feedback.excluded_photo_ids
    )
    entries = _archive_entries(
        tuple(photo for photo in photos if photo.pk not in feedback.manual_photo_ids)
    )
    if not entries or len(photos) < 2:
        return _not_found_response()
    return prepare_archive_response(
        scope=f"search:{search.pk}",
        entries=entries,
        filename=f"findme-photo-{search.event.slug}-search-results.zip",
        observation=ArchiveObservation(context="free_result", page=1),
    )


def _event_page(request, event_slug: str, form: SelfieSearchUploadForm, *, status: int = 200):
    from config.views import event_detail

    response = event_detail(request, event_slug, selfie_search_form=form)
    response.status_code = status
    return response


def _archive_entries(photos: tuple[Photo, ...]) -> tuple[ArchiveEntry, ...] | None:
    entries: list[ArchiveEntry] = []
    for photo in photos:
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

_SELFIE_PASS_ERRORS = {
    "no_face": "На добавленном селфи не удалось найти подходящее лицо.",
    "multiple_faces": "На добавленном селфи должно быть только одно лицо.",
    "quality_rejected": "На добавленном селфи не удалось распознать лицо достаточно чётко.",
    "search_unavailable": "По добавленному селфи сейчас не удалось выполнить поиск.",
    "failed": "Не удалось продолжить поиск по добавленному селфи.",
}


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
