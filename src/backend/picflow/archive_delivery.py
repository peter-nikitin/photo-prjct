from collections.abc import Callable, Sequence
from uuid import UUID

from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from ingestion.storage import StorageUnavailable

from picflow.archive import ArchiveEntry, ArchiveObservation
from picflow.archive_jobs import enqueue_archive_job, find_archive_job, sign_archive_job
from picflow.models import PhotoArchiveJob


def prepare_archive_response(
    *,
    scope: str,
    entries: Sequence[ArchiveEntry],
    filename: str,
    observation: ArchiveObservation,
    order_item_ids: Sequence[int] = (),
    audit_request: Callable[[], None] | None = None,
) -> HttpResponse:
    """Enqueue an authorized page and return its private status URL."""
    try:
        with transaction.atomic():
            job = enqueue_archive_job(
                scope=scope,
                entries=entries,
                filename=filename,
                observation=observation,
                order_item_ids=order_item_ids,
            )
            if audit_request is not None:
                audit_request()
    except ValueError:
        return HttpResponse(status=503)
    return HttpResponseRedirect(
        reverse("photo_archive_status", kwargs={"public_id": job.public_id}),
        status=303,
    )


@require_GET
def archive_status(request: HttpRequest, public_id: UUID) -> HttpResponse:
    job = find_archive_job(public_id)
    if job is None:
        return HttpResponse(status=404)
    if job.status == PhotoArchiveJob.Status.READY and request.GET.get("download") == "1":
        try:
            return HttpResponseRedirect(sign_archive_job(job))
        except StorageUnavailable:
            return HttpResponse(status=503)

    pending = job.status in (PhotoArchiveJob.Status.QUEUED, PhotoArchiveJob.Status.RUNNING)
    status_url = reverse("photo_archive_status", kwargs={"public_id": public_id})
    response = render(
        request,
        "ui/archive_status.html",
        {
            "pending": pending,
            "ready": job.status == PhotoArchiveJob.Status.READY,
            "status_url": status_url,
            "yandex_metrika_counter_id": None,
            "web_vitals_page": None,
        },
    )
    if pending:
        response["Refresh"] = "3"
    return response
