"""Role-scoped private event workspace reads and mutations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db.models import F, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from ingestion.services.batch_history import (
    BATCH_HISTORY_PAGE_SIZE,
    owned_event_batch_history,
    owned_event_batch_summaries,
)
from ingestion.services.resume import list_unfinished_batches
from ingestion.workspace_context import upload_workspace_context
from processing.photo_status import (
    PhotoProcessingDetail,
    photo_processing_details,
)

from picflow.event_management import (
    EventManagementCapabilities,
    apply_event_photo_action,
    event_management_capabilities,
    event_photo_queryset,
)
from picflow.event_management_access import (
    can_inspect_event_photos,
    can_upload_event_photos,
    event_management_denial,
)
from picflow.event_management_forms import (
    EventFolderCreateForm,
    EventFolderDeleteForm,
    EventFolderRenameForm,
    EventPhotoActionForm,
    EventPhotoFilterForm,
    EventPhotoPageForm,
)
from picflow.event_management_media import with_admin_thumbnail
from picflow.models import Event, Photo


@dataclass(frozen=True)
class AdminPhotoCard:
    id: str
    filename: str
    capture_time: datetime | None
    photographer: str | None
    folder_id: int | None
    folder_name: str | None
    is_hidden: bool
    thumbnail_url: str | None
    original_url: str
    processing: PhotoProcessingDetail


def admin_photo_page(photos: QuerySet[Photo], *, page: str | int = 1) -> Page[AdminPhotoCard]:
    """Materialize one authorized page with application URLs and no storage identities."""
    selected = Paginator(
        photos.select_related("folder", "uploaded_by").order_by(
            F("capture_time").asc(nulls_last=True), "pk"
        ),
        100,
    ).get_page(page)
    rows = list(selected)
    page_photos = Photo.objects.filter(pk__in=[photo.pk for photo in rows])
    thumbnails = set(with_admin_thumbnail(page_photos).values_list("pk", flat=True))
    details = {row["photo_id"]: row for row in photo_processing_details(page_photos)}
    cards = [
        AdminPhotoCard(
            id=photo.pk,
            filename=photo.original_filename or photo.pk,
            capture_time=photo.capture_time,
            photographer=photo.uploaded_by.get_username() if photo.uploaded_by else None,
            folder_id=photo.folder_id,
            folder_name=photo.folder.name if photo.folder else None,
            is_hidden=photo.is_hidden,
            thumbnail_url=(
                reverse("event_management_media", args=[photo.event_id, photo.pk, "thumbnail"])
                if photo.pk in thumbnails
                else None
            ),
            original_url=reverse(
                "event_management_media", args=[photo.event_id, photo.pk, "original"]
            ),
            processing=details[photo.pk],
        )
        for photo in rows
    ]
    return Page(cards, selected.number, selected.paginator)


def _private_event(request: HttpRequest, event_id: int) -> tuple[Event | None, HttpResponse | None]:
    denial = event_management_denial(request, admin_only=True)
    if denial is not None:
        return None, denial
    try:
        return Event.objects.get(pk=event_id), None
    except Event.DoesNotExist:
        return None, HttpResponse(status=404)


def _canonical_url(event: Event, filter_query: str, page: int) -> str:
    values = filter_query
    if page > 1:
        values = f"{values}&page={page}" if values else f"page={page}"
    path = reverse("event_management", args=[event.pk])
    return f"{path}?{values}" if values else path


def _admin_results_context(
    event: Event, data: Any, *, capabilities: EventManagementCapabilities
) -> dict[str, object]:
    filter_form = EventPhotoFilterForm(event, data=data)
    page_form = EventPhotoPageForm(data=data)
    filters_valid = filter_form.is_valid() and page_form.is_valid()
    if filters_valid:
        photos = event_photo_queryset(event, filter_form.filters)
        photo_page = admin_photo_page(photos, page=page_form.cleaned_data["page"])
        canonical_query = filter_form.canonical_query
    else:
        photo_page = admin_photo_page(Photo.objects.none())
        canonical_query = ""
    canonical_url = _canonical_url(event, canonical_query, photo_page.number)
    return {
        "folders": event.folders.all(),
        "filter_form": filter_form,
        "page_form": page_form,
        "filters_valid": filters_valid,
        "photo_page": photo_page,
        "capabilities": capabilities,
        "canonical_query": canonical_query,
        "canonical_url": canonical_url,
        "results_url": reverse("event_management_results", args=[event.pk]),
        "folder_create_url": reverse("event_management_folder_create", args=[event.pk]),
        "folder_rename_url": reverse("event_management_folder_rename", args=[event.pk]),
        "folder_delete_url": reverse("event_management_folder_delete", args=[event.pk]),
        "photo_action_url": reverse("event_management_action", args=[event.pk]),
    }


@never_cache
@require_GET
def event_management(request: HttpRequest, event_id: int) -> HttpResponse:
    denial = event_management_denial(request)
    if denial is not None:
        return denial
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return HttpResponse(status=404)
    can_inspect = can_inspect_event_photos(request.user)
    can_upload = can_upload_event_photos(request.user)
    context: dict[str, object] = {
        "event": event,
        "yandex_metrika_counter_id": None,
        "folders": event.folders.all(),
        "can_inspect": can_inspect,
        "can_upload": can_upload,
        "batch_history_url": reverse("event_management_batch_history", args=[event.pk]),
    }
    status = 200
    if can_upload:
        context.update(upload_workspace_context(request))
        batch_page = owned_event_batch_history(
            uploader=request.user, event=event, page=request.GET.get("batch_page", "1")
        )
        context["batch_page"] = batch_page
        unfinished = list_unfinished_batches(
            request.user, event=event, batch_ids=tuple(row.id for row in batch_page)
        )
        context["unfinished_batches"] = unfinished
        context["resumable_batch_ids"] = {row.id for row in unfinished}
    if can_inspect:
        context.update(
            _admin_results_context(
                event, request.GET, capabilities=event_management_capabilities(request.user)
            )
        )
        if not context["filters_valid"]:
            status = 400
    return TemplateResponse(request, "picflow/event_management.html", context, status=status)


@never_cache
@require_GET
def event_management_batch_history(request: HttpRequest, event_id: int) -> HttpResponse:
    """Render one bounded owner/event history fragment with the active batch pinned."""
    denial = event_management_denial(request)
    if denial is not None:
        return denial
    if not can_upload_event_photos(request.user):
        return HttpResponseForbidden()
    batch_values = request.GET.getlist("batch_id")
    if len(batch_values) != 1:
        return JsonResponse({"error": "invalid_batch_id"}, status=400)
    try:
        batch_id = UUID(batch_values[0])
    except (AttributeError, TypeError, ValueError):
        return JsonResponse({"error": "invalid_batch_id"}, status=400)
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return HttpResponse(status=404)
    requested = owned_event_batch_summaries(
        uploader=request.user,
        event=event,
        batch_ids=(batch_id,),
    )
    if not requested:
        return HttpResponse(status=404)
    first_page = owned_event_batch_history(uploader=request.user, event=event)
    rows = [requested[0]]
    rows.extend(row for row in first_page if row.id != batch_id)
    rows = rows[:BATCH_HISTORY_PAGE_SIZE]
    batch_page = Page(rows, 1, first_page.paginator)
    unfinished = list_unfinished_batches(
        request.user,
        event=event,
        batch_ids=tuple(row.id for row in rows),
    )
    return TemplateResponse(
        request,
        "ingestion/_batch_history_fragment.html",
        {
            "batch_page": batch_page,
            "resumable_batch_ids": {row.id for row in unfinished},
        },
    )


@never_cache
@require_GET
def event_management_results(request: HttpRequest, event_id: int) -> HttpResponse:
    event, error = _private_event(request, event_id)
    if error is not None:
        return error
    assert event is not None
    context = {
        "event": event,
        **_admin_results_context(
            event, request.GET, capabilities=event_management_capabilities(request.user)
        ),
    }
    status = 200 if context["filters_valid"] else 422
    response = TemplateResponse(
        request, "picflow/_event_photo_results.html", context, status=status
    )
    response["X-Event-Photo-Canonical-Url"] = str(context["canonical_url"])
    return response


def _form_errors(form: Any) -> dict[str, list[str]]:
    return {
        name: [item["message"] for item in errors]
        for name, errors in form.errors.get_json_data(escape_html=True).items()
    }


def _folder_payload(event: Event) -> list[dict[str, int | str]]:
    return list(event.folders.values("id", "name"))


@never_cache
@require_POST
def event_management_folder_create(request: HttpRequest, event_id: int) -> HttpResponse:
    event, error = _private_event(request, event_id)
    if error is not None:
        return error
    assert event is not None
    if not event_management_capabilities(request.user).can_add_folders:
        return HttpResponseForbidden()
    form = EventFolderCreateForm(event, request.POST)
    if form.save() is None:
        return JsonResponse({"errors": _form_errors(form)}, status=422)
    return JsonResponse({"folders": _folder_payload(event)})


@never_cache
@require_POST
def event_management_folder_rename(request: HttpRequest, event_id: int) -> HttpResponse:
    event, error = _private_event(request, event_id)
    if error is not None:
        return error
    assert event is not None
    if not event_management_capabilities(request.user).can_change_folders:
        return HttpResponseForbidden()
    form = EventFolderRenameForm(event, request.POST)
    if form.save() is None:
        return JsonResponse({"errors": _form_errors(form)}, status=422)
    return JsonResponse({"folders": _folder_payload(event)})


@never_cache
@require_POST
def event_management_folder_delete(request: HttpRequest, event_id: int) -> HttpResponse:
    event, error = _private_event(request, event_id)
    if error is not None:
        return error
    assert event is not None
    if not event_management_capabilities(request.user).can_delete_folders:
        return HttpResponseForbidden()
    form = EventFolderDeleteForm(event, request.POST)
    if not form.delete():
        return JsonResponse({"errors": _form_errors(form)}, status=422)
    return JsonResponse(
        {
            "folders": _folder_payload(event),
            "deleted_folder_id": int(request.POST["folder"]),
        }
    )


@never_cache
@require_POST
def event_management_action(request: HttpRequest, event_id: int) -> HttpResponse:
    event, error = _private_event(request, event_id)
    if error is not None:
        return error
    assert event is not None
    if not event_management_capabilities(request.user).can_change_photos:
        return HttpResponseForbidden()
    form = EventPhotoActionForm(event, request.POST)
    if not form.is_valid():
        return JsonResponse({"errors": _form_errors(form)}, status=422)
    try:
        changed_count = apply_event_photo_action(
            event,
            form.selection,
            form.cleaned_data["action"],
            form.cleaned_data["target_folder"],
        )
    except ValidationError as validation_error:
        return JsonResponse({"errors": {"selection": validation_error.messages}}, status=422)
    return JsonResponse({"changed_count": changed_count})
