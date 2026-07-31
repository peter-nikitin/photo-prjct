from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TypedDict

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Case, Count, IntegerField, Value, When
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from picflow.models import Event

from processing.models import EventProcessingRun, ProcessingJob


class ProcessingRunRow(TypedDict):
    event: Event
    event_admin_url: str
    processor: str
    status: str
    total: int
    counts: dict[str, int]
    processed: int
    remaining: int
    eta: str


STATUSES = tuple(ProcessingJob.Status.values)
TERMINAL_STATUSES = (
    ProcessingJob.Status.SUCCEEDED,
    ProcessingJob.Status.FAILED,
    ProcessingJob.Status.CANCELLED,
)
REMAINING_STATUSES = (
    ProcessingJob.Status.QUEUED,
    ProcessingJob.Status.PROCESSING,
    ProcessingJob.Status.RETRY_WAIT,
)


def _eta(
    run: EventProcessingRun, processing_jobs: Iterable[ProcessingJob], remaining: int, now: datetime
) -> str:
    if run.status == EventProcessingRun.Status.CLOSED:
        return "Completed"

    current_jobs = list(processing_jobs)
    if len(current_jobs) != 1 or current_jobs[0].claimed_at is None:
        return "—"

    elapsed = max(now - current_jobs[0].claimed_at, timedelta())
    return f"{(now + elapsed * remaining).strftime('%Y-%m-%d %H:%M')} UTC"


@require_GET
@staff_member_required
def admin_processing_progress(request):
    now = timezone.now()
    runs = EventProcessingRun.objects.select_related("event").order_by(
        Case(
            When(status=EventProcessingRun.Status.CLOSED, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        "-created_at",
    )
    rows: list[ProcessingRunRow] = []
    for run in runs:
        counts = {status: 0 for status in STATUSES}
        counts.update(
            {
                row["status"]: row["count"]
                for row in run.jobs.values("status").annotate(count=Count("id"))
                if row["status"] in counts
            }
        )
        processed = sum(counts[status] for status in TERMINAL_STATUSES)
        remaining = sum(counts[status] for status in REMAINING_STATUSES)
        rows.append(
            {
                "event": run.event,
                "event_admin_url": reverse("admin:picflow_event_change", args=[run.event_id]),
                "processor": f"{run.processor_type} v{run.processor_version}",
                "status": run.status,
                "total": sum(counts.values()),
                "counts": counts,
                "processed": processed,
                "remaining": remaining,
                "eta": _eta(
                    run,
                    run.jobs.filter(status=ProcessingJob.Status.PROCESSING).only("claimed_at"),
                    remaining,
                    now,
                ),
            }
        )
    return render(request, "processing/admin_progress.html", {"rows": rows, "statuses": STATUSES})
