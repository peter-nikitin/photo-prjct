from uuid import UUID

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from ingestion.forms import BatchCreateForm
from ingestion.models import UploadBatch
from ingestion.services.batches import BatchConflict, BatchInput, create_batch
from ingestion.views import (
    _json_form,
    _not_found,
    _service_error,
    _upload_access,
    upload_items_register,
)
from picflow.models import Event

from agent_api.auth import error
from agent_api.models import AgentUpload


class AgentBatchForm(BatchCreateForm):
    client_task_id = forms.UUIDField()


def start(request: HttpRequest) -> JsonResponse:
    if not settings.PHOTO_UPLOAD_ENABLED:
        return error("not_found", "Photo upload is unavailable.", 404)
    if not (
        request.user.has_perm("ingestion.upload_photos")
        and request.user.has_perm("picflow.view_event")
    ):
        return error("permission_denied", "You do not have permission to upload photos.", 403)
    data, invalid = _json_form(request, AgentBatchForm)
    if invalid is not None:
        return invalid
    assert data is not None

    with transaction.atomic():
        owner = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        existing = (
            AgentUpload.objects.select_related("batch")
            .filter(owner=owner, client_task_id=data["client_task_id"])
            .first()
        )
        if existing is not None:
            batch = existing.batch
            if (
                batch.event_id != data["event_id"]
                or batch.expected_item_count != data["expected_item_count"]
            ):
                return error("task_conflict", "This task has different upload metadata.", 409)
            return batch_response(batch.pk, batch.status, batch.expected_item_count, status=200)
        event = Event.objects.filter(pk=data["event_id"]).first()
        if event is None:
            return error("not_found", "The event was not found.", 404)
        try:
            result = create_batch(
                uploader=owner,
                event=event,
                data=BatchInput(expected_item_count=data["expected_item_count"]),
            )
        except BatchConflict as exc:
            return _service_error(exc)
        AgentUpload.objects.create(
            owner=owner, client_task_id=data["client_task_id"], batch_id=result.id
        )
        return batch_response(result.id, result.status, result.expected, status=201)


def batch_response(batch_id, state: str, expected: int, *, status: int) -> JsonResponse:
    return JsonResponse(
        {"batch": {"id": str(batch_id), "status": state, "expected_item_count": expected}},
        status=status,
    )


@_upload_access(json_errors=True)
def register(request: HttpRequest, batch: UUID) -> JsonResponse:
    with transaction.atomic():
        owned = (
            UploadBatch.objects.select_for_update()
            .filter(pk=batch, uploader_id=request.user.pk)
            .first()
        )
        if owned is None:
            return _not_found()

        # Only stale cleanup can make a partly registered batch terminal.
        # Lock the same batch row as cleanup and ingestion through the registration.
        reopening = (
            owned.status in (UploadBatch.Status.FAILED, UploadBatch.Status.PARTIAL)
            and AgentUpload.objects.filter(batch=owned, owner_id=request.user.pk).exists()
            and owned.items.count() < owned.expected_item_count
        )
        if reopening:
            owned.status = UploadBatch.Status.UPLOADING
            owned.completed_at = None
            owned.save(update_fields=["status", "completed_at"])
        response = upload_items_register(request, batch)
        if reopening and response.status_code >= 400:
            transaction.set_rollback(True)
        return response
