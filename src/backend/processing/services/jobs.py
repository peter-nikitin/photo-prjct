"""Transactional processing transitions with one run-to-attempt lock order."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from processing.contracts import AttemptCompletion, ClaimedJob, CompletionConflict, EmptyClaim
from processing.models import (
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)

DEFAULT_LEASE_SECONDS = 120
DEFAULT_RECOVERY_LIMIT = 25
MAX_ATTEMPTS = 3
DEFAULT_RETRY_POLICY = {
    "max_attempts": MAX_ATTEMPTS,
    "base_backoff_seconds": 30,
    "max_backoff_seconds": 300,
    "jitter_seconds": 5,
    "lease_max_seconds": 300,
}


def claim_job(
    *,
    contract_version: int,
    processor_type: str,
    processor_version: int,
    worker_build: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: timezone.datetime | None = None,
) -> ClaimedJob | EmptyClaim:
    """Atomically seal and lease compatible work; skip races until no work remains."""
    now = now or timezone.now()
    tried: set[UUID] = set()
    with transaction.atomic():
        while True:
            candidate = (
                ProcessingJob.objects.filter(
                    contract_version=contract_version,
                    processor_type=processor_type,
                    processor_version=processor_version,
                    status__in=(ProcessingJob.Status.QUEUED, ProcessingJob.Status.RETRY_WAIT),
                    available_at__lte=now,
                    run__status__in=(
                        EventProcessingRun.Status.COLLECTING,
                        EventProcessingRun.Status.SEALED,
                    ),
                )
                .exclude(pk__in=tried)
                .order_by("available_at", "created_at", "id")
                .first()
            )
            if candidate is None:
                return EmptyClaim()
            tried.add(candidate.id)
            run = EventProcessingRun.objects.select_for_update().get(pk=candidate.run_id)
            job = (
                ProcessingJob.objects.select_for_update(skip_locked=True)
                .filter(
                    pk=candidate.pk,
                    status__in=(ProcessingJob.Status.QUEUED, ProcessingJob.Status.RETRY_WAIT),
                    available_at__lte=now,
                )
                .first()
            )
            if job is None or run.status == EventProcessingRun.Status.CLOSED:
                continue
            _validate_lease_seconds(lease_seconds, job.configuration)
            if run.status == EventProcessingRun.Status.COLLECTING:
                run.status = EventProcessingRun.Status.SEALED
                run.sealed_at = now
                run.save(update_fields=["status", "sealed_at"])
            state = PhotoProcessingState.objects.select_for_update().get(
                photo_id=job.photo_id,
                processor_type=job.processor_type,
            )
            if state.current_job_id != job.id:
                continue
            attempt = ProcessingAttempt.objects.create(
                event_id=job.event_id,
                run=run,
                job=job,
                photo_id=job.photo_id,
                contract_version=job.contract_version,
                processor_type=job.processor_type,
                processor_version=job.processor_version,
                configuration=job.configuration,
                input_fingerprint=job.input_fingerprint,
                worker_build=worker_build,
                claimed_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            job.status = ProcessingJob.Status.PROCESSING
            job.claimed_at = now
            job.save(update_fields=["status", "claimed_at"])
            _set_state(
                state,
                status=PhotoProcessingState.Status.PROCESSING,
                current_attempt=attempt,
                processing_at=now,
                next_attempt_at=None,
            )
            return ClaimedJob(job=job, attempt=attempt)


def heartbeat_attempt(
    attempt_id: UUID,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: timezone.datetime | None = None,
) -> ProcessingAttempt | None:
    now = now or timezone.now()
    with transaction.atomic():
        _, job, state, attempt = _locked_context(attempt_id)
        _validate_lease_seconds(lease_seconds, job.configuration)
        if not _owns_current_lease(state, attempt, now):
            return None
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.save(update_fields=["heartbeat_at", "lease_expires_at"])
        return attempt


def refresh_download(
    attempt_id: UUID, *, now: timezone.datetime | None = None
) -> ProcessingAttempt | None:
    now = now or timezone.now()
    with transaction.atomic():
        _, _, state, attempt = _locked_context(attempt_id)
        return attempt if _owns_current_lease(state, attempt, now) else None


def complete_attempt(
    attempt_id: UUID,
    *,
    result: dict[str, Any],
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
    now: timezone.datetime | None = None,
    jitter: Callable[[int, int], int] | None = None,
) -> AttemptCompletion:
    now = now or timezone.now()
    payload = {
        "outcome": "success",
        "result": result,
        "download_duration_ms": download_duration_ms,
        "compute_duration_ms": compute_duration_ms,
        "total_duration_ms": total_duration_ms,
        "worker_started_at": worker_started_at,
        "worker_finished_at": worker_finished_at,
    }
    return _terminal_submission(attempt_id, payload, now=now, jitter=jitter)


def fail_attempt(
    attempt_id: UUID,
    *,
    error_code: str,
    error_detail: str = "",
    canonical_error_detail: str | None = None,
    retryable: bool,
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
    now: timezone.datetime | None = None,
    jitter: Callable[[int, int], int] | None = None,
) -> AttemptCompletion:
    now = now or timezone.now()
    payload = {
        "outcome": "failure",
        "error_code": error_code,
        "error_detail": error_detail,
        "canonical_error_detail": canonical_error_detail,
        "retryable": retryable,
        "download_duration_ms": download_duration_ms,
        "compute_duration_ms": compute_duration_ms,
        "total_duration_ms": total_duration_ms,
        "worker_started_at": worker_started_at,
        "worker_finished_at": worker_finished_at,
    }
    return _terminal_submission(attempt_id, payload, now=now, jitter=jitter)


def recover_expired_attempts(
    *,
    now: timezone.datetime | None = None,
    jitter: Callable[[int, int], int] | None = None,
    limit: int = DEFAULT_RECOVERY_LIMIT,
) -> list[ProcessingAttempt]:
    if limit < 1:
        raise ValueError("limit must be positive")
    now = now or timezone.now()
    candidate_ids = list(
        ProcessingAttempt.objects.filter(
            status=ProcessingAttempt.Status.IN_PROGRESS,
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    recovered: list[ProcessingAttempt] = []
    for attempt_id in candidate_ids:
        with transaction.atomic():
            run, job, state, attempt = _locked_context(attempt_id)
            if attempt.status != ProcessingAttempt.Status.IN_PROGRESS:
                continue
            if attempt.lease_expires_at is None or attempt.lease_expires_at > now:
                continue
            _recover_expired(run, job, state, attempt, now=now, jitter=jitter)
            recovered.append(attempt)
    return recovered


def _terminal_submission(
    attempt_id: UUID,
    payload: dict[str, Any],
    *,
    now: timezone.datetime,
    jitter: Callable[[int, int], int] | None,
) -> AttemptCompletion:
    payload_hash = _canonical_hash(payload)
    with transaction.atomic():
        run, job, state, attempt = _locked_context(attempt_id)
        if attempt.status != ProcessingAttempt.Status.IN_PROGRESS:
            if attempt.status == ProcessingAttempt.Status.EXPIRED:
                return _record_late_receipt(attempt, payload, payload_hash, now)
            return _duplicate(attempt, payload_hash)
        if not _owns_current_lease(state, attempt, now):
            if attempt.lease_expires_at is not None and attempt.lease_expires_at <= now:
                _recover_expired(run, job, state, attempt, now=now, jitter=jitter)
                return _record_late_receipt(attempt, payload, payload_hash, now)
            _terminal_stale(attempt, payload, payload_hash, now)
            return AttemptCompletion(attempt=attempt, stale=True)
        if payload["outcome"] == "success":
            _terminal_success(attempt, payload, now)
            job.status = ProcessingJob.Status.SUCCEEDED
            job.completed_at = now
            job.save(update_fields=["status", "completed_at"])
            _set_state(
                state,
                status=PhotoProcessingState.Status.SUCCEEDED,
                current_attempt=attempt,
                accepted_attempt=attempt,
                succeeded_at=now,
                next_attempt_at=None,
            )
        else:
            _terminal_failure(attempt, payload, now)
            _transition_failure(run, job, state, attempt, now=now, jitter=jitter)
        _close_run_if_terminal(run.id, now)
        return AttemptCompletion(attempt=attempt)


def _locked_context(
    attempt_id: UUID,
) -> tuple[EventProcessingRun, ProcessingJob, PhotoProcessingState, ProcessingAttempt]:
    identity = ProcessingAttempt.objects.only("run_id", "job_id").get(pk=attempt_id)
    run = EventProcessingRun.objects.select_for_update().get(pk=identity.run_id)
    job = ProcessingJob.objects.select_for_update().get(pk=identity.job_id)
    state = PhotoProcessingState.objects.select_for_update().get(
        photo_id=job.photo_id,
        processor_type=job.processor_type,
    )
    attempt = ProcessingAttempt.objects.select_for_update().get(pk=attempt_id)
    return run, job, state, attempt


def _recover_expired(
    run: EventProcessingRun,
    job: ProcessingJob,
    state: PhotoProcessingState,
    attempt: ProcessingAttempt,
    *,
    now: timezone.datetime,
    jitter: Callable[[int, int], int] | None,
) -> None:
    payload = {
        "outcome": "failure",
        "error_code": "lease_expired",
        "error_detail": "",
        "retryable": True,
    }
    attempt.status = ProcessingAttempt.Status.EXPIRED
    attempt.terminal_at = now
    attempt.result = {
        key: value for key, value in payload.items() if key != "canonical_error_detail"
    }
    attempt.result_hash = _canonical_hash(payload)
    attempt.error_code = "lease_expired"
    attempt.error_detail = ""
    attempt.accepted = False
    attempt.save(
        update_fields=[
            "status",
            "terminal_at",
            "result",
            "result_hash",
            "error_code",
            "error_detail",
            "accepted",
        ]
    )
    if state.current_attempt_id == attempt.id:
        _transition_failure(run, job, state, attempt, now=now, jitter=jitter)
    _close_run_if_terminal(run.id, now)


def _transition_failure(
    run: EventProcessingRun,
    job: ProcessingJob,
    state: PhotoProcessingState,
    attempt: ProcessingAttempt,
    *,
    now: timezone.datetime,
    jitter: Callable[[int, int], int] | None,
) -> None:
    attempts_used = ProcessingAttempt.objects.filter(job=job).count()
    retryable = bool(attempt.result.get("retryable"))
    if retryable and attempts_used < _policy(job.configuration)["max_attempts"]:
        retry_at = now + timedelta(
            seconds=_backoff_seconds(job.configuration, attempts_used, jitter)
        )
        job.status = ProcessingJob.Status.RETRY_WAIT
        job.available_at = retry_at
        job.save(update_fields=["status", "available_at"])
        _set_state(
            state,
            status=PhotoProcessingState.Status.RETRY_WAIT,
            current_attempt=None,
            next_attempt_at=retry_at,
        )
        return
    job.status = ProcessingJob.Status.FAILED
    job.completed_at = now
    job.save(update_fields=["status", "completed_at"])
    _set_state(
        state,
        status=PhotoProcessingState.Status.FAILED,
        current_attempt=attempt,
        failed_at=now,
        next_attempt_at=None,
    )


def _record_late_receipt(
    attempt: ProcessingAttempt,
    payload: dict[str, Any],
    payload_hash: str,
    now: timezone.datetime,
) -> AttemptCompletion:
    existing = ProcessingLateReceipt.objects.select_for_update().filter(attempt=attempt).first()
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise CompletionConflict(
                "A different late payload was already recorded for this attempt.",
                attempt_id=attempt.id,
                submitted_hash=payload_hash,
            )
        return AttemptCompletion(attempt=attempt, idempotent=True, stale=True)
    try:
        with transaction.atomic():
            ProcessingLateReceipt.objects.create(
                attempt=attempt,
                received_at=now,
                payload=_durable_payload(payload),
                payload_hash=payload_hash,
            )
    except IntegrityError:
        existing = ProcessingLateReceipt.objects.filter(attempt=attempt).first()
        if existing is not None and existing.payload_hash == payload_hash:
            return AttemptCompletion(attempt=attempt, idempotent=True, stale=True)
        raise CompletionConflict(
            "A different late payload was concurrently recorded for this attempt.",
            attempt_id=attempt.id,
            submitted_hash=payload_hash,
        ) from None
    return AttemptCompletion(attempt=attempt, stale=True)


def _owns_current_lease(
    state: PhotoProcessingState, attempt: ProcessingAttempt, now: timezone.datetime
) -> bool:
    return bool(
        state.current_attempt_id == attempt.id
        and attempt.lease_expires_at is not None
        and attempt.lease_expires_at > now
    )


def _set_state(
    state: PhotoProcessingState,
    *,
    status: Any,
    current_attempt: ProcessingAttempt | None,
    accepted_attempt: ProcessingAttempt | None | object = ...,
    processing_at: timezone.datetime | None | object = ...,
    succeeded_at: timezone.datetime | None | object = ...,
    failed_at: timezone.datetime | None | object = ...,
    next_attempt_at: timezone.datetime | None | object = ...,
) -> None:
    values: dict[str, object] = {"status": status, "current_attempt": current_attempt}
    for field, value in {
        "accepted_attempt": accepted_attempt,
        "processing_at": processing_at,
        "succeeded_at": succeeded_at,
        "failed_at": failed_at,
        "next_attempt_at": next_attempt_at,
    }.items():
        if value is not ...:
            values[field] = value
    for field, value in values.items():
        setattr(state, field, value)
    state.save(update_fields=[*values, "updated_at"])


def _terminal_success(
    attempt: ProcessingAttempt, payload: dict[str, Any], now: timezone.datetime
) -> None:
    attempt.status = ProcessingAttempt.Status.SUCCEEDED
    attempt.terminal_at = now
    attempt.result = payload["result"]
    attempt.result_hash = _canonical_hash(payload)
    attempt.download_duration_ms = payload["download_duration_ms"]
    attempt.compute_duration_ms = payload["compute_duration_ms"]
    attempt.total_duration_ms = payload["total_duration_ms"]
    attempt.worker_started_at = payload["worker_started_at"]
    attempt.worker_finished_at = payload["worker_finished_at"]
    attempt.accepted = True
    attempt.save(
        update_fields=[
            "status",
            "terminal_at",
            "result",
            "result_hash",
            "download_duration_ms",
            "compute_duration_ms",
            "total_duration_ms",
            "worker_started_at",
            "worker_finished_at",
            "accepted",
        ]
    )


def _terminal_failure(
    attempt: ProcessingAttempt, payload: dict[str, Any], now: timezone.datetime
) -> None:
    attempt.status = ProcessingAttempt.Status.FAILED
    attempt.terminal_at = now
    attempt.result = _durable_payload(payload)
    attempt.result_hash = _canonical_hash(payload)
    attempt.error_code = str(payload["error_code"])
    attempt.error_detail = str(payload["error_detail"])
    attempt.download_duration_ms = payload["download_duration_ms"]
    attempt.compute_duration_ms = payload["compute_duration_ms"]
    attempt.total_duration_ms = payload["total_duration_ms"]
    attempt.worker_started_at = payload["worker_started_at"]
    attempt.worker_finished_at = payload["worker_finished_at"]
    attempt.accepted = False
    attempt.save(
        update_fields=[
            "status",
            "terminal_at",
            "result",
            "result_hash",
            "error_code",
            "error_detail",
            "download_duration_ms",
            "compute_duration_ms",
            "total_duration_ms",
            "worker_started_at",
            "worker_finished_at",
            "accepted",
        ]
    )


def _terminal_stale(
    attempt: ProcessingAttempt,
    payload: dict[str, Any],
    payload_hash: str,
    now: timezone.datetime,
) -> None:
    attempt.status = ProcessingAttempt.Status.STALE
    attempt.terminal_at = now
    attempt.result = _durable_payload(payload)
    attempt.result_hash = payload_hash
    attempt.accepted = False
    attempt.save(update_fields=["status", "terminal_at", "result", "result_hash", "accepted"])


def _duplicate(attempt: ProcessingAttempt, payload_hash: str) -> AttemptCompletion:
    if attempt.result_hash != payload_hash:
        raise CompletionConflict(
            "A different terminal payload was already recorded for this attempt.",
            attempt_id=attempt.id,
            submitted_hash=payload_hash,
        )
    return AttemptCompletion(
        attempt=attempt,
        idempotent=True,
        stale=attempt.status == ProcessingAttempt.Status.STALE,
    )


def _policy(configuration: dict[str, Any]) -> dict[str, int]:
    configured = configuration.get("retry_policy", {})
    return {key: int(configured.get(key, value)) for key, value in DEFAULT_RETRY_POLICY.items()}


def _backoff_seconds(
    configuration: dict[str, Any],
    attempts_used: int,
    jitter: Callable[[int, int], int] | None,
) -> int:
    policy = _policy(configuration)
    base = min(
        policy["max_backoff_seconds"],
        policy["base_backoff_seconds"] * 2 ** (attempts_used - 1),
    )
    offset = (jitter or random.randint)(0, policy["jitter_seconds"])
    return min(policy["max_backoff_seconds"], base + max(0, min(offset, policy["jitter_seconds"])))


def _validate_lease_seconds(lease_seconds: int, configuration: dict[str, Any]) -> None:
    worker = configuration.get("worker")
    configured = worker.get("lease_duration_seconds") if isinstance(worker, dict) else None
    if (
        not isinstance(configured, int)
        or isinstance(configured, bool)
        or configured < 1
        or configured > _policy(configuration)["lease_max_seconds"]
    ):
        raise ValueError("configuration has no valid lease duration")
    if lease_seconds != configured:
        raise ValueError("lease_seconds must equal the configured lease duration")


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _durable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "canonical_error_detail"}


def _close_run_if_terminal(run_id: UUID, now: timezone.datetime) -> None:
    from processing.services.reports import close_run_report

    close_run_report(run_id, now=now)
