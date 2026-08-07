"""Immutable event-scoped run reports built from persisted processing evidence."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from processing.models import (
    FACE_EMBEDDING_BENCHMARK_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    JSON_MAX_BYTES,
    REPORT_JSON_MAX_BYTES,
    EventProcessingRun,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.face_quality import (
    QUALITY_REJECTION_REASONS,
    TECHNICAL_FAILURE_REASONS,
)

_PREVIEW_WARNING_CODES = frozenset({"color_profile_missing"})
_PREVIEW_FAILURE_CODES = frozenset(
    {
        "decode_failed",
        "download_authorization_expired",
        "fingerprint_mismatch",
        "input_too_large",
        "invalid_dimensions",
        "network_interruption",
        "normalization_failed",
        "output_contract_violation",
        "storage_unavailable",
        "unsupported_input",
    }
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
    attempts: dict[str, int] = {
        "total": ProcessingAttempt.objects.filter(run=run).count(),
        "retries": sum(max(row["attempt_count"] - 1, 0) for row in rows),
    }
    report: dict[str, Any] = {
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
        "attempts": attempts,
        "faces": _faces_report_summary(rows),
        "started_at": run.created_at.isoformat(),
        "finished_at": now.isoformat(),
        "total_duration_ms": max(0, round((now - run.created_at).total_seconds() * 1000)),
        "durations_ms": _duration_summary(durations),
        "photos": rows,
    }
    if run.processor_type == GENERATE_PREVIEW_PROCESSOR:
        attempts["stale"] = ProcessingAttempt.objects.filter(
            run=run, status=ProcessingAttempt.Status.STALE
        ).count()
        report["preview"] = _preview_report(rows)
    return report


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
    faces = _face_embedding_counts(accepted)
    row: dict[str, Any] = {
        "status": job.status,
        "accepted_attempt_id": str(accepted.id) if accepted else None,
        "capture_time_present": capture_time_present,
        "attempt_count": len(attempts),
        "faces_detected": faces["detected"],
        "faces_embedded": faces["embedded"],
        "faces_kept": faces["kept"],
        "faces_quality_rejected": faces["quality_rejected"],
        "faces_technical_failed": faces["technical_failed"],
        "face_rejection_reasons": faces["rejection_reasons"],
        "face_technical_failure_reasons": faces["technical_failure_reasons"],
        "duration_ms": accepted.total_duration_ms if accepted else None,
        "warnings": [
            str(code)[: _row_limits(job.configuration)["max_warning_chars"]]
            for code in warnings[: _row_limits(job.configuration)["max_warnings"]]
        ],
        "error_code": (terminal.error_code if terminal else "")[:64],
    }
    if job.processor_type != FACE_EMBEDDING_BENCHMARK_PROCESSOR:
        row["photo_id"] = job.photo_id
    if job.processor_type == GENERATE_PREVIEW_PROCESSOR:
        row["preview"] = _preview_row(result, accepted=accepted)
    return row


def _preview_row(
    result: dict[str, Any], *, accepted: ProcessingAttempt | None
) -> dict[str, int | None] | None:
    """Return only verified numeric output facts; never relay worker result payloads."""
    fields = ("byte_size", "width", "height", "upload_ms")
    if accepted is None or not all(
        isinstance(result.get(field), int)
        and not isinstance(result[field], bool)
        and result[field] >= 0
        for field in fields
    ):
        return None
    return {
        **{field: result[field] for field in fields},
        "download_ms": _bounded_duration(accepted.download_duration_ms),
        "compute_ms": _bounded_duration(accepted.compute_duration_ms),
    }


def _preview_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    previews = [row["preview"] for row in rows if isinstance(row.get("preview"), dict)]
    warnings = {
        code: sum(code in row["warnings"] for row in rows)
        for code in sorted(_PREVIEW_WARNING_CODES)
    }
    failures = {
        code: sum(row["error_code"] == code for row in rows)
        for code in sorted(_PREVIEW_FAILURE_CODES)
    }
    return {
        "accepted_outputs": len(previews),
        "output_bytes": _duration_summary([preview["byte_size"] for preview in previews]),
        "output_width": _duration_summary([preview["width"] for preview in previews]),
        "output_height": _duration_summary([preview["height"] for preview in previews]),
        "upload_durations_ms": _duration_summary([preview["upload_ms"] for preview in previews]),
        "download_durations_ms": _duration_summary(
            [preview["download_ms"] for preview in previews if preview["download_ms"] is not None]
        ),
        "compute_durations_ms": _duration_summary(
            [preview["compute_ms"] for preview in previews if preview["compute_ms"] is not None]
        ),
        "warnings": {code: count for code, count in warnings.items() if count},
        "failure_codes": {code: count for code, count in failures.items() if count},
    }


def _bounded_duration(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _faces_report_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "denominator": len(rows),
        "detected": sum(row["faces_detected"] for row in rows),
        "kept": sum(row["faces_kept"] for row in rows),
        "quality_rejected": sum(row["faces_quality_rejected"] for row in rows),
        "embedded": sum(row["faces_embedded"] for row in rows),
        "technical_failed": sum(row["faces_technical_failed"] for row in rows),
        "rejection_reasons": _aggregate_face_reasons(
            rows, "face_rejection_reasons", QUALITY_REJECTION_REASONS
        ),
        "technical_failure_reasons": _aggregate_face_reasons(
            rows,
            "face_technical_failure_reasons",
            TECHNICAL_FAILURE_REASONS,
        ),
    }


def _face_embedding_counts(attempt: ProcessingAttempt | None) -> dict[str, Any]:
    if attempt is None:
        return {
            "detected": 0,
            "kept": 0,
            "quality_rejected": 0,
            "embedded": 0,
            "technical_failed": 0,
            "rejection_reasons": {},
            "technical_failure_reasons": {},
        }
    detections = list(
        PhotoFaceDetection.objects.filter(attempt=attempt)
        .order_by("face_index")
        .values("status", "features", "embedding__id")
    )
    rejection_reasons: Counter[str] = Counter()
    technical_failure_reasons: Counter[str] = Counter()
    for detection in detections:
        features = detection["features"]
        if not isinstance(features, dict):
            continue
        if detection["status"] == PhotoFaceDetection.Status.QUALITY_REJECTED:
            quality = features.get("quality")
            reasons = quality.get("reasons") if isinstance(quality, dict) else None
            if isinstance(reasons, list):
                rejection_reasons.update(
                    reason
                    for reason in reasons
                    if isinstance(reason, str) and reason in QUALITY_REJECTION_REASONS
                )
        elif detection["status"] == PhotoFaceDetection.Status.FAILED:
            error_code = features.get("error_code")
            if isinstance(error_code, str) and error_code in TECHNICAL_FAILURE_REASONS:
                technical_failure_reasons[error_code] += 1
    return {
        "detected": len(detections),
        "kept": sum(
            detection["status"] == PhotoFaceDetection.Status.KEPT for detection in detections
        ),
        "quality_rejected": sum(
            detection["status"] == PhotoFaceDetection.Status.QUALITY_REJECTED
            for detection in detections
        ),
        "embedded": sum(detection["embedding__id"] is not None for detection in detections),
        "technical_failed": sum(
            detection["status"] == PhotoFaceDetection.Status.FAILED for detection in detections
        ),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "technical_failure_reasons": dict(sorted(technical_failure_reasons.items())),
    }


def _aggregate_face_reasons(
    rows: list[dict[str, Any]], field: str, allowed: frozenset[str]
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        reasons = row.get(field)
        if not isinstance(reasons, dict):
            continue
        for reason, count in reasons.items():
            if reason in allowed and isinstance(count, int) and not isinstance(count, bool):
                counts[reason] += max(0, count)
    return dict(sorted(counts.items()))


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
    preview_summary_json = 4_096 if isinstance(configuration.get("generate_preview"), dict) else 0
    return 2 * (
        JSON_MAX_BYTES
        + cohort * attempts * worker_build_json
        + cohort * row_json
        + preview_summary_json
        + 4_096
    )


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
