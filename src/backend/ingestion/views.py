from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any
from uuid import UUID

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.views import LoginView, LogoutView, redirect_to_login
from django.core.exceptions import PermissionDenied
from django.forms import Form
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from picflow.models import Event

from ingestion.forms import (
    AuthorizationForm,
    BatchCreateForm,
    EmptyJsonForm,
    FailureForm,
    ItemRegistrationForm,
)
from ingestion.models import UploadBatch, UploadItem
from ingestion.services.batches import (
    AuthorizationReason,
    BatchConflict,
    BatchInput,
    BatchResult,
    ItemInput,
    ItemResult,
    ItemStateConflict,
    authorize_item,
    create_batch,
    derive_batch_status,
    register_items,
    report_item_failed,
)
from ingestion.services.confirmation import confirm_upload_item
from ingestion.storage import PrivateUploadStorage, StorageError

UploadView = Callable[..., HttpResponse]


class PhotographerLoginView(LoginView):
    template_name = "admin/login.html"
    redirect_authenticated_user = True

    def get_success_url(self) -> str:
        requested = self.request.POST.get(REDIRECT_FIELD_NAME) or self.request.GET.get(
            REDIRECT_FIELD_NAME
        )
        if requested and url_has_allowed_host_and_scheme(
            requested,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return requested
        return reverse("upload_page")


class PhotographerLogoutView(LogoutView):
    next_page = "/"


def _upload_access(*, json_errors: bool = False) -> Callable[[UploadView], UploadView]:
    def decorate(view: UploadView) -> UploadView:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
            if not settings.PHOTO_UPLOAD_ENABLED:
                if json_errors:
                    return _not_found()
                raise Http404
            if not request.user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(),
                    reverse("photographer_login"),
                    REDIRECT_FIELD_NAME,
                )
            if not request.user.has_perm("ingestion.upload_photos"):
                if json_errors:
                    return _error(
                        "permission_denied",
                        "You do not have permission to upload photos.",
                        status=403,
                    )
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorate


@require_GET
@_upload_access()
def upload_page(request: HttpRequest) -> HttpResponse:
    return HttpResponse()


@require_POST
@_upload_access(json_errors=True)
def upload_batch_create(request: HttpRequest) -> JsonResponse:
    data, error = _json_form(request, BatchCreateForm)
    if error is not None:
        return error
    assert data is not None
    try:
        event = Event.objects.get(pk=data["event_id"])
    except Event.DoesNotExist:
        return _error("not_found", "The event was not found.", status=404)
    try:
        result = create_batch(
            uploader=request.user,
            event=event,
            data=BatchInput(expected_item_count=data["expected_item_count"]),
        )
    except BatchConflict as exc:
        return _service_error(exc)
    return JsonResponse(
        {
            "batch": {
                "id": str(result.id),
                "status": result.status,
                "expected_item_count": result.expected,
            }
        },
        status=201,
    )


@require_POST
@_upload_access(json_errors=True)
def upload_items_register(request: HttpRequest, batch: UUID) -> JsonResponse:
    if _owned_batch(request, batch) is None:
        return _not_found()
    data, error = _json_form(request, ItemRegistrationForm)
    if error is not None:
        return error
    assert data is not None
    inputs = [
        ItemInput(
            client_item_id=item["client_item_id"],
            filename=item["filename"],
            content_type=item["content_type"],
            size=item["size"],
        )
        for item in data["items"]
    ]
    try:
        result = register_items(uploader=request.user, batch_id=batch, items=inputs)
    except UploadBatch.DoesNotExist:
        return _not_found()
    except BatchConflict as exc:
        return _service_error(exc)
    return JsonResponse(
        {
            "items": [
                {
                    "id": str(item.id),
                    "client_item_id": str(item.client_item_id),
                    "status": item.status,
                }
                for item in result.items
            ]
        },
        status=201 if result.created else 200,
    )


@require_POST
@_upload_access(json_errors=True)
def upload_item_authorize(request: HttpRequest, batch: UUID, item: UUID) -> JsonResponse:
    if _owned_item(request, batch, item) is None:
        return _not_found()
    data, error = _json_form(request, AuthorizationForm)
    if error is not None:
        return error
    assert data is not None
    try:
        result = authorize_item(
            uploader=request.user,
            batch_id=batch,
            item_id=item,
            reason=AuthorizationReason(data["reason"]),
            storage=PrivateUploadStorage(),
        )
    except (UploadBatch.DoesNotExist, UploadItem.DoesNotExist):
        return _not_found()
    except (BatchConflict, ItemStateConflict) as exc:
        return _service_error(exc)
    except StorageError:
        return _storage_unavailable()
    return JsonResponse(
        {
            "item": {
                "id": str(result.item.id),
                "status": result.item.status,
                "attempt": result.item.attempt,
            },
            "grant": {
                "url": result.grant.url,
                "fields": result.grant.fields,
                "expires_at": result.grant.expires_at.isoformat(),
            },
        }
    )


@require_POST
@_upload_access(json_errors=True)
def upload_item_confirm(request: HttpRequest, batch: UUID, item: UUID) -> JsonResponse:
    if _owned_item(request, batch, item) is None:
        return _not_found()
    _, error = _json_form(request, EmptyJsonForm)
    if error is not None:
        return error
    try:
        photo = confirm_upload_item(
            uploader=request.user,
            batch_id=batch,
            item_id=item,
            storage=PrivateUploadStorage(),
        )
    except (UploadBatch.DoesNotExist, UploadItem.DoesNotExist):
        return _not_found()
    except (BatchConflict, ItemStateConflict) as exc:
        return _service_error(exc)
    except StorageError:
        return _storage_unavailable()
    if photo is None:
        state = _owned_item(request, batch, item)
        if state is None:
            return _not_found()
        if state.error_code in {"storage_unavailable", "object_missing", "object_changed"}:
            return _storage_unavailable()
        return _error(
            state.error_code or "confirmation_failed",
            state.sanitized_error_message or "The upload could not be confirmed.",
            status=409,
        )
    return JsonResponse(
        {"item": {"id": str(item), "status": "uploaded", "photo_id": str(photo.pk)}}
    )


@require_POST
@_upload_access(json_errors=True)
def upload_item_failed(request: HttpRequest, batch: UUID, item: UUID) -> JsonResponse:
    if _owned_item(request, batch, item) is None:
        return _not_found()
    data, error = _json_form(request, FailureForm)
    if error is not None:
        return error
    assert data is not None
    try:
        result = report_item_failed(
            uploader=request.user,
            batch_id=batch,
            item_id=item,
            code=data["code"],
        )
    except (UploadBatch.DoesNotExist, UploadItem.DoesNotExist):
        return _not_found()
    except (BatchConflict, ItemStateConflict) as exc:
        return _service_error(exc)
    return JsonResponse({"item": _failed_item_payload(result)})


@require_POST
@_upload_access(json_errors=True)
def upload_batch_finalize(request: HttpRequest, batch: UUID) -> JsonResponse:
    if _owned_batch(request, batch) is None:
        return _not_found()
    _, error = _json_form(request, EmptyJsonForm)
    if error is not None:
        return error
    try:
        result = derive_batch_status(uploader=request.user, batch_id=batch, finalize=True)
    except UploadBatch.DoesNotExist:
        return _not_found()
    except BatchConflict as exc:
        return _service_error(exc)
    return JsonResponse({"batch": _final_batch_payload(result)})


def _json_form(
    request: HttpRequest, form_class: type[Form]
) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        parsed = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, _error("invalid_json", "Request body must be valid JSON.", status=400)
    if not isinstance(parsed, dict):
        return None, _error("invalid_json", "Request body must be a JSON object.", status=400)
    form = form_class(parsed)
    if not form.is_valid():
        fields = {
            field: [entry["message"] for entry in entries]
            for field, entries in form.errors.get_json_data().items()
        }
        return None, _error(
            "validation_error",
            "Request validation failed.",
            fields=fields,
            status=400,
        )
    return form.cleaned_data, None


def _owned_batch(request: HttpRequest, batch_id: UUID) -> UploadBatch | None:
    return UploadBatch.objects.filter(id=batch_id, uploader_id=request.user.pk).first()


def _owned_item(request: HttpRequest, batch_id: UUID, item_id: UUID) -> UploadItem | None:
    return UploadItem.objects.filter(
        id=item_id,
        batch_id=batch_id,
        batch__uploader_id=request.user.pk,
    ).first()


def _error(
    code: str,
    message: str,
    *,
    status: int,
    fields: dict[str, list[str]] | None = None,
) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": code, "message": message, "fields": fields or {}}},
        status=status,
    )


def _not_found() -> JsonResponse:
    return _error("not_found", "The upload resource was not found.", status=404)


def _storage_unavailable() -> JsonResponse:
    return _error(
        "storage_unavailable",
        "Object storage is temporarily unavailable.",
        status=503,
    )


def _service_error(exc: BatchConflict | ItemStateConflict) -> JsonResponse:
    return _error(exc.code, exc.message, status=409)


def _failed_item_payload(item: ItemResult) -> dict[str, object]:
    return {"id": str(item.id), "status": item.status, "error_code": item.error_code}


def _final_batch_payload(batch: BatchResult) -> dict[str, object]:
    return {
        "id": str(batch.id),
        "status": batch.status,
        "expected": batch.expected,
        "uploaded": batch.uploaded,
        "failed": batch.failed,
    }
