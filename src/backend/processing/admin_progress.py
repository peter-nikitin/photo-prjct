from datetime import datetime, timedelta
from typing import TypedDict

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from picflow.models import Event

from processing.models import ProcessingJob

PROCESSORS = (
    ("generate_preview", "Preview"),
    ("generate_watermarked_preview", "Watermark"),
    ("face_embedding", "Embedding"),
    ("capture_metadata", "Metadata"),
)
TERMINAL_STATUSES = {
    ProcessingJob.Status.SUCCEEDED,
    ProcessingJob.Status.FAILED,
    ProcessingJob.Status.CANCELLED,
}
RUNNABLE_STATUSES = {ProcessingJob.Status.QUEUED, ProcessingJob.Status.RETRY_WAIT}
REMAINING_STATUSES = RUNNABLE_STATUSES | {ProcessingJob.Status.PROCESSING}


class WorkerSummary(TypedDict):
    name: str
    completed: int
    total: int
    status: str
    eta: str
    remaining: int


class EventSummaryRow(TypedDict):
    event: Event
    event_admin_url: str
    total: int
    status: str
    workers: list[WorkerSummary]


def _worker_eta(current_jobs: list[ProcessingJob], remaining: int, now: datetime) -> str:
    if len(current_jobs) != 1:
        return "—"

    elapsed = max(now - current_jobs[0].claimed_at, timedelta())
    return f"{(now + elapsed * remaining).strftime('%Y-%m-%d %H:%M')} UTC"


def _worker_summary(
    *,
    name: str,
    jobs: list[ProcessingJob],
    total: int,
    preview_remaining: int,
    is_embedding: bool,
    now: datetime,
) -> WorkerSummary:
    completed = sum(job.status in TERMINAL_STATUSES for job in jobs)
    remaining = sum(job.status in REMAINING_STATUSES for job in jobs)
    current_jobs = [
        job
        for job in jobs
        if job.status == ProcessingJob.Status.PROCESSING and job.claimed_at is not None
    ]
    runnable = any(job.status in RUNNABLE_STATUSES for job in jobs)
    is_completed = completed == total and remaining == 0

    if total == 0:
        status, eta = "Not applicable", "—"
    elif is_completed:
        status, eta = "Completed", "Completed"
    elif len(current_jobs) == 1:
        status, eta = "Processing", _worker_eta(current_jobs, remaining, now)
    elif runnable:
        status, eta = "Queued", "—"
    elif is_embedding and preview_remaining:
        status, eta = "Waiting for preview", "Waiting for preview"
    else:
        status, eta = "Not started", "—"

    return {
        "name": name,
        "completed": completed,
        "total": total,
        "status": status,
        "eta": eta,
        "remaining": remaining,
    }


@require_GET
@staff_member_required
def admin_processing_progress(request):
    now = timezone.now()
    jobs_by_event: dict[int, list[ProcessingJob]] = {}
    events: dict[int, Event] = {}
    jobs = ProcessingJob.objects.filter(processor_type__in=dict(PROCESSORS)).select_related("event")
    for job in jobs:
        jobs_by_event.setdefault(job.event_id, []).append(job)
        events[job.event_id] = job.event

    rows: list[EventSummaryRow] = []
    for event_id, event_jobs in jobs_by_event.items():
        total = len({job.photo_id for job in event_jobs})
        jobs_by_processor = {
            processor_type: [job for job in event_jobs if job.processor_type == processor_type]
            for processor_type, _ in PROCESSORS
        }
        preview_remaining = sum(
            job.status in REMAINING_STATUSES for job in jobs_by_processor["generate_preview"]
        )
        watermark_total = len(
            {job.photo_id for job in jobs_by_processor["generate_watermarked_preview"]}
        )
        workers = [
            _worker_summary(
                name=name,
                jobs=jobs_by_processor[processor_type],
                total=(
                    watermark_total if processor_type == "generate_watermarked_preview" else total
                ),
                preview_remaining=preview_remaining,
                is_embedding=processor_type == "face_embedding",
                now=now,
            )
            for processor_type, name in PROCESSORS
        ]
        event_completed = all(
            worker["completed"] == worker["total"] and worker["remaining"] == 0
            for worker in workers
        )
        rows.append(
            {
                "event": events[event_id],
                "event_admin_url": reverse("admin:picflow_event_change", args=[event_id]),
                "total": total,
                "status": "Completed" if event_completed else "In progress",
                "workers": workers,
            }
        )

    return render(request, "processing/admin_progress.html", {"rows": rows})
