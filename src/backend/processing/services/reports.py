"""Immutable event-scoped run reports built from persisted processing evidence."""

from __future__ import annotations

from statistics import median
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from processing.models import (
    JSON_MAX_BYTES,
    REPORT_JSON_MAX_BYTES,
    EventProcessingRun,
    ProcessingAttempt,
    ProcessingJob,
)


def close_run_report(
    run_id: UUID, *, now: timezone.datetime | None = None
) -> EventProcessingRun | None:
    """Close a sealed run only after each exact cohort member is terminal."""
    now = now or timezone.now()
    with transaction.atomic():
        run = EventProcessingRun.objects.select_for_update().get(pk=run_id)
        if run.status == EventProcessingRun.Status.CLOSED:
            return run
        if run.status != EventProcessingRun.Status.SEALED:
            return None
        jobs = list(ProcessingJob.objects.select_for_update().filter(run=run).order_by("photo_id"))
        terminal = {
            ProcessingJob.Status.SUCCEEDED,
            ProcessingJob.Status.FAILED,
            ProcessingJob.Status.CANCELLED,
        }
        if any(job.status not in terminal for job in jobs):
            return None
        report = _report_payload(run, jobs, now)
        if _serialized_bytes(report) > _report_limit(run):
            raise ValueError("The configured cohort does not fit in its bounded processing report.")
        run.report = report
        run.status = EventProcessingRun.Status.CLOSED
        run.closed_at = now
        run.save(update_fields=["report", "status", "closed_at"])
        return run


def _report_payload(
    run: EventProcessingRun, jobs: list[ProcessingJob], now: timezone.datetime
) -> dict[str, Any]:
    rows = [_photo_row(job) for job in jobs]
    statuses = [job.status for job in jobs]
    successful = [row for row in rows if row["status"] == ProcessingJob.Status.SUCCEEDED]
    durations = [row["duration_ms"] for row in successful if row["duration_ms"] is not None]
    return {
        "event_id": str(run.event_id),
        "run_id": str(run.id),
        "processor": {
            "contract_version": run.contract_version,
            "type": run.processor_type,
            "version": run.processor_version,
            "configuration": run.configuration,
        },
        "worker_builds": sorted(
            set(
                ProcessingAttempt.objects.filter(run=run)
                .exclude(worker_build="")
                .values_list("worker_build", flat=True)
            )
        )[: _max_attempts(run) * _cohort_limit(run)],
        "cohort_size": len(jobs),
        "counts": {
            "denominator": len(jobs),
            "succeeded": statuses.count(ProcessingJob.Status.SUCCEEDED),
            "failed": statuses.count(ProcessingJob.Status.FAILED),
            "cancelled": statuses.count(ProcessingJob.Status.CANCELLED),
        },
        "capture_time": {
            "denominator": len(successful),
            "with_capture_time": sum(row["capture_time_present"] is True for row in successful),
            "without_capture_time": sum(row["capture_time_present"] is False for row in successful),
        },
        "attempts": {
            "total": ProcessingAttempt.objects.filter(run=run).count(),
            "retries": sum(max(row["attempt_count"] - 1, 0) for row in rows),
        },
        "started_at": run.created_at.isoformat(),
        "finished_at": now.isoformat(),
        "total_duration_ms": max(0, round((now - run.created_at).total_seconds() * 1000)),
        "durations_ms": _duration_summary(durations),
        "photos": rows,
    }


def _photo_row(job: ProcessingJob) -> dict[str, Any]:
    attempts = list(job.attempts.order_by("created_at", "id"))
    accepted = next((attempt for attempt in attempts if attempt.accepted), None)
    terminal = next((attempt for attempt in reversed(attempts) if attempt.terminal_at), None)
    result = accepted.result if accepted is not None else {}
    warnings = result.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    capture_time_present: bool | None = None
    if accepted is not None and "capture_time" in result:
        capture_time_present = result["capture_time"] is not None
    return {
        "photo_id": job.photo_id,
        "status": job.status,
        "accepted_attempt_id": str(accepted.id) if accepted else None,
        "capture_time_present": capture_time_present,
        "attempt_count": len(attempts),
        "duration_ms": accepted.total_duration_ms if accepted else None,
        "warnings": [
            str(code)[: _row_limits(job.configuration)["max_warning_chars"]]
            for code in warnings[: _row_limits(job.configuration)["max_warnings"]]
        ],
        "error_code": (terminal.error_code if terminal else "")[:64],
    }


def _duration_summary(durations: list[int]) -> dict[str, int | float | None]:
    if not durations:
        return {"denominator": 0, "min": None, "median": None, "max": None}
    return {
        "denominator": len(durations),
        "min": min(durations),
        "median": median(durations),
        "max": max(durations),
    }


def _report_limit(run: EventProcessingRun) -> int:
    return min(
        int(run.configuration.get("report_max_bytes", REPORT_JSON_MAX_BYTES)), REPORT_JSON_MAX_BYTES
    )


def report_upper_bound_bytes(configuration: dict[str, Any]) -> int:
    """Conservative byte bound for the normalized configuration's valid report shape.

    Every unvalidated character is conservatively six JSON bytes (``\\u001f``).  The generic
    configuration payload is itself capped at ``JSON_MAX_BYTES``; reports retain at most 20 rows,
    three worker builds per row, eight warning codes, one 32-char photo id, one 64-char error code,
    and a UUID accepted-attempt id.  Doubling the summed bound reserves JSON keys, dates, counts,
    separators, and future fixed fields while remaining below the explicit 256 KiB report cap.
    """
    cohort = _cohort_limit_from_configuration(configuration)
    attempts = _max_attempts_from_configuration(configuration)
    row_limits = _row_limits(configuration)
    escaped = 6
    worker_build_json = 2 + 128 * escaped + 1
    warning_json = 2 + row_limits["max_warning_chars"] * escaped + 1
    row_json = 512 + 32 * escaped + 36 + 64 * escaped + row_limits["max_warnings"] * warning_json
    return 2 * (JSON_MAX_BYTES + cohort * attempts * worker_build_json + cohort * row_json + 4_096)


def _cohort_limit(run: EventProcessingRun) -> int:
    return _cohort_limit_from_configuration(run.configuration)


def _max_attempts(run: EventProcessingRun) -> int:
    return _max_attempts_from_configuration(run.configuration)


def _cohort_limit_from_configuration(configuration: dict[str, Any]) -> int:
    return int(configuration.get("max_cohort_size", 20))


def _max_attempts_from_configuration(configuration: dict[str, Any]) -> int:
    return int(configuration.get("retry_policy", {}).get("max_attempts", 3))


def _row_limits(configuration: dict[str, Any]) -> dict[str, int]:
    configured = configuration.get("report_row_limits", {})
    return {
        "max_warnings": int(configured.get("max_warnings", 8)),
        "max_warning_chars": int(configured.get("max_warning_chars", 32)),
    }


def _serialized_bytes(report: dict[str, Any]) -> int:
    import json

    return len(
        json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    )
