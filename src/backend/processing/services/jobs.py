"""Transactional processing transitions with one run-to-attempt lock order."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone
from picflow.models import Event, Photo

from processing.contracts import AttemptCompletion, ClaimedJob, CompletionConflict, EmptyClaim
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)
from processing.services.face_quality import (
    QUALITY_FACE_CONTRACT_VERSION,
    QUALITY_FACE_PROCESSOR_VERSIONS,
    ValidatedQualityResult,
    publish_face_embedding_projection,
    quality_face_result_geometry,
    validate_quality_face_result,
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


class PreviewPublicationRequired(ValueError):
    """Preview success must pass the storage-verifying publication service."""


def claim_job(
    *,
    contract_version: int,
    processor_type: str,
    processor_version: int,
    worker_build: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: timezone.datetime | None = None,
    event_id: int | None = None,
    configuration_hash: str | None = None,
) -> ClaimedJob | EmptyClaim:
    """Atomically seal and lease compatible, optionally exact-scoped work."""
    now = now or timezone.now()
    tried: set[UUID] = set()
    with transaction.atomic():
        while True:
            candidates = ProcessingJob.objects.filter(
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
            if event_id is not None:
                candidates = candidates.filter(event_id=event_id)
            if configuration_hash is not None:
                candidates = candidates.filter(configuration_hash=configuration_hash)
            candidate = (
                candidates.exclude(pk__in=tried)
                .order_by("available_at", "created_at", "id")
                .first()
            )
            if candidate is None:
                return EmptyClaim()
            tried.add(candidate.id)
            _lock_transition_row(Event, pk=candidate.event_id)
            run = _lock_transition_row(EventProcessingRun, pk=candidate.run_id)
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
            _lock_transition_row(Photo, pk=job.photo_id)
            _validate_lease_seconds(lease_seconds, job.configuration)
            if run.status == EventProcessingRun.Status.COLLECTING:
                run.status = EventProcessingRun.Status.SEALED
                run.sealed_at = now
                run.save(update_fields=["status", "sealed_at"])
            state = _lock_transition_row(
                PhotoProcessingState,
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
        _, _, job, _, state, attempt = _locked_context(attempt_id)
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
        _, _, _, _, state, attempt = _locked_context(attempt_id)
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
    processor_type = (
        ProcessingAttempt.objects.filter(pk=attempt_id)
        .values_list("processor_type", flat=True)
        .first()
    )
    if processor_type in {
        GENERATE_PREVIEW_PROCESSOR,
        GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    }:
        raise PreviewPublicationRequired(
            "Preview attempts require verified preview publication before completion."
        )
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
            _, run, job, _, state, attempt = _locked_context(attempt_id)
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
    now: timezone.datetime | None,
    jitter: Callable[[int, int], int] | None,
) -> AttemptCompletion:
    payload_hash = _canonical_hash(payload)
    with transaction.atomic():
        _, run, job, photo, state, attempt = _locked_context(attempt_id)
        now = now or timezone.now()
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
            if attempt.processor_type == CAPTURE_METADATA_PROCESSOR:
                transition_capture_time_projection(
                    photo=photo,
                    state=state,
                    accepted_attempt=attempt if attempt.processor_version == 2 else None,
                )
        else:
            _terminal_failure(attempt, payload, now)
            _transition_failure(run, job, state, attempt, now=now, jitter=jitter)
            if attempt.processor_type == CAPTURE_METADATA_PROCESSOR:
                transition_capture_time_projection(photo=photo, state=state, accepted_attempt=None)
        _close_locked_run_if_terminal(run, now)
        return AttemptCompletion(attempt=attempt)


def _locked_context(
    attempt_id: UUID,
) -> tuple[
    Event, EventProcessingRun, ProcessingJob, Photo, PhotoProcessingState, ProcessingAttempt
]:
    identity = ProcessingAttempt.objects.only("event_id", "run_id", "job_id", "photo_id").get(
        pk=attempt_id
    )
    event = _lock_transition_row(Event, pk=identity.event_id)
    run = _lock_transition_row(EventProcessingRun, pk=identity.run_id)
    jobs = _lock_transition_rows(ProcessingJob, run_id=identity.run_id)
    job = next(job for job in jobs if job.pk == identity.job_id)
    photo = _lock_transition_row(Photo, pk=identity.photo_id)
    state = _lock_transition_row(
        PhotoProcessingState,
        photo_id=job.photo_id,
        processor_type=job.processor_type,
    )
    attempt = _lock_transition_row(ProcessingAttempt, pk=attempt_id)
    return event, run, job, photo, state, attempt


def _lock_transition_row(model: Any, **lookup: Any) -> Any:
    """Take one lifecycle row lock; callers keep the documented global order."""
    return model.objects.select_for_update().get(**lookup)


def _lock_transition_rows(model: Any, **lookup: Any) -> list[Any]:
    """Lock one same-kind lifecycle row group in a deterministic order."""
    return list(model.objects.select_for_update().filter(**lookup).order_by("photo_id", "id"))


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
    _close_locked_run_if_terminal(run, now)


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


def transition_capture_time_projection(
    *, photo: Photo, state: PhotoProcessingState, accepted_attempt: ProcessingAttempt | None
) -> None:
    """Synchronously clear or publish the sole supported Photo time projection."""
    capture_time = None
    source_attempt = None
    if accepted_attempt is not None:
        if not (
            accepted_attempt.photo_id == photo.pk
            and state.photo_id == photo.pk
            and state.processor_type == CAPTURE_METADATA_PROCESSOR
            and state.status == PhotoProcessingState.Status.SUCCEEDED
            and state.current_run_id == accepted_attempt.run_id
            and state.current_job_id == accepted_attempt.job_id
            and state.current_attempt_id == accepted_attempt.id
            and state.accepted_attempt_id == accepted_attempt.id
            and accepted_attempt.processor_type == CAPTURE_METADATA_PROCESSOR
            and accepted_attempt.processor_version == 2
            and accepted_attempt.status == ProcessingAttempt.Status.SUCCEEDED
            and accepted_attempt.accepted
        ):
            raise ValueError("capture-time projection requires the current accepted v2 attempt")
        value = accepted_attempt.result.get("capture_time")
        if value is not None:
            if not isinstance(value, str):
                raise ValueError("capture-time projection requires a canonical timestamp")
            try:
                capture_time = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError as error:
                raise ValueError(
                    "capture-time projection requires a canonical timestamp"
                ) from error
            source_attempt = accepted_attempt
    photo.capture_time = capture_time
    photo.capture_time_source_attempt = source_attempt
    photo.save(update_fields=["capture_time", "capture_time_source_attempt"])


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
    if attempt.processor_type == FACE_EMBEDDING_PROCESSOR:
        if _persist_face_embedding_result(attempt, payload["result"]):
            publish_face_embedding_projection(attempt)


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


def _persist_face_embedding_result(attempt: ProcessingAttempt, result: dict[str, Any]) -> bool:
    if (
        attempt.contract_version == QUALITY_FACE_CONTRACT_VERSION
        and attempt.processor_version in QUALITY_FACE_PROCESSOR_VERSIONS
    ):
        validated = validate_quality_face_result(
            result,
            configuration=attempt.configuration,
        )
        input_geometry = quality_face_result_geometry(attempt, result)
        _persist_quality_face_result(attempt, validated, input_geometry=input_geometry)
        return True
    if not isinstance(result, dict):
        return False
    model = _coerce_face_model(result, attempt.configuration)
    input_geometry = _face_input_geometry(attempt, result)
    faces = result.get("faces")
    if not isinstance(faces, list):
        return False
    artifact = FaceProcessingAttemptArtifact.objects.create(
        attempt=attempt,
        status=FaceProcessingAttemptArtifact.Status.COMPLETE,
        feature_payload={
            "model": model,
            "warnings": _safe_json_list(result.get("warnings"), maximum=8),
            "timings": _safe_json_dict(result.get("timings")),
            "face_count": len(faces),
            "has_single_query_face_usable": bool(result.get("has_single_query_face_usable", False)),
        },
        quality_payload={
            "quality": result.get("quality"),
            "model": model,
        },
    )
    for face in _iter_face_records(result):
        index, record = face
        if index is None:
            continue
        detection = PhotoFaceDetection.objects.create(
            attempt=attempt,
            artifact=artifact,
            face_index=index,
            status=PhotoFaceDetection.Status.KEPT,
            geometry={
                "bbox": _safe_face_bbox(record.get("bbox")),
                "landmarks": record.get("landmarks", []),
                "model": model,
            }
            | input_geometry,
            features={
                "confidence": record.get("quality"),
                "quality": record.get("quality"),
                "warnings": _safe_json_list(record.get("quality_flags"), maximum=8),
                "source": record.get("source", "model"),
            },
        )
        embedding = record.get("embedding")
        if embedding is not None:
            FaceEmbedding.objects.create(
                detection=detection,
                model_version=model,
                vector=embedding,
                metadata=_safe_dict(record),
            )
    return True


def _persist_quality_face_result(
    attempt: ProcessingAttempt,
    result: ValidatedQualityResult,
    *,
    input_geometry: dict[str, Any],
) -> None:
    artifact = FaceProcessingAttemptArtifact.objects.create(
        attempt=attempt,
        status=FaceProcessingAttemptArtifact.Status.COMPLETE,
        feature_payload={
            "model": result.model,
            "warnings": result.warnings,
            "timings": result.timings,
            "detected_count": result.detected_count,
            "kept_count": result.kept_count,
            "quality_rejected_count": result.quality_rejected_count,
            "embedded_count": result.embedded_count,
            "technical_failed_count": result.technical_failed_count,
            "truncated": "faces_truncated" in result.warnings,
            "has_single_query_face_usable": result.has_single_query_face_usable,
        },
        quality_payload={
            "rejection_reasons": result.rejection_reasons,
            "technical_failure_reasons": result.technical_failure_reasons,
        },
    )
    status_by_result = {
        "kept": PhotoFaceDetection.Status.KEPT,
        "quality_rejected": PhotoFaceDetection.Status.QUALITY_REJECTED,
        "technical_failed": PhotoFaceDetection.Status.FAILED,
    }
    for face in result.faces:
        features: dict[str, Any] = {"confidence": face.confidence}
        if face.quality is not None:
            features["quality"] = face.quality
        if face.error_code is not None:
            features["error_code"] = face.error_code
        detection = PhotoFaceDetection.objects.create(
            attempt=attempt,
            artifact=artifact,
            face_index=face.index,
            status=status_by_result[face.status],
            geometry={
                "bbox": face.bbox,
                "landmarks": face.landmarks,
                "model": result.model,
            }
            | input_geometry,
            features=features,
        )
        if face.embedding is not None:
            FaceEmbedding.objects.create(
                detection=detection,
                model_version=result.model,
                vector=face.embedding,
                metadata={
                    "confidence": face.confidence,
                    "quality": face.quality,
                },
            )


def _face_input_geometry(attempt: ProcessingAttempt, result: dict[str, Any]) -> dict[str, Any]:
    """Persist explicit preview coordinates only when they bind the accepted derivative."""
    if attempt.contract_version != 2:
        return {}
    geometry = result.get("input_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("preview face result is missing input geometry")
    derivative = PhotoDerivative.objects.filter(
        photo_id=attempt.photo_id,
        variant="preview-small-v1",
        accepted_attempt_id__isnull=False,
    ).first()
    expected = (
        {
            "coordinate_space": "preview-small-v1",
            "pixel_width": derivative.width,
            "pixel_height": derivative.height,
            "oriented_source_width": derivative.oriented_source_width,
            "oriented_source_height": derivative.oriented_source_height,
        }
        if derivative is not None
        else None
    )
    if geometry != expected:
        raise ValueError("preview face result geometry disagrees with the accepted derivative")
    return {
        **expected,
        "scale_x": expected["oriented_source_width"] / expected["pixel_width"],
        "scale_y": expected["oriented_source_height"] / expected["pixel_height"],
    }


def _iter_face_records(result: dict[str, Any]) -> list[tuple[int | None, dict[str, Any]]]:
    faces = result.get("faces")
    if not isinstance(faces, list):
        return []
    records: list[tuple[int | None, dict[str, Any]]] = []
    for index, raw in enumerate(faces):
        if not isinstance(raw, dict):
            continue
        if {"face_id", "bbox", "quality", "embedding_sha256"}.issubset(raw):
            quality = raw.get("quality")
            bbox = _safe_face_bbox(raw.get("bbox"))
            if bbox is None:
                continue
            vector = _coerce_embedding(raw.get("embedding"))
            if vector is None:
                vector = _maybe_embedding_from_sha256(raw.get("embedding_sha256"))
            if quality is None or not isinstance(quality, (int, float)) or not (0 <= quality <= 1):
                continue
            records.append(
                (
                    index,
                    {
                        "index": index,
                        "bbox": bbox,
                        "quality": quality,
                        "embedding": vector,
                        "landmarks": [],
                        "confidence": quality,
                        "source": "legacy",
                    },
                )
            )
            continue
        if {
            "index",
            "bbox",
            "confidence",
            "embedding",
            "landmarks",
        }.issubset(raw):
            raw_bbox = _safe_face_bbox(raw.get("bbox"))
            if raw_bbox is None:
                continue
            quality = raw.get("confidence")
            quality = quality if isinstance(quality, (int, float)) and 0 <= quality <= 1 else None
            if quality is None:
                continue
            embedding = _coerce_embedding(raw.get("embedding"))
            if embedding is None:
                continue
            landmarks = raw.get("landmarks")
            if not (
                isinstance(landmarks, (list, tuple))
                and len(landmarks) == 5
                and all(
                    isinstance(point, (list, tuple))
                    and len(point) == 2
                    and all(_safe_face_coordinate(item) for item in point)
                    for point in landmarks
                )
            ):
                continue
            records.append(
                (
                    _coerce_int(raw.get("index")),
                    {
                        "index": raw.get("index"),
                        "bbox": raw_bbox,
                        "quality": quality,
                        "embedding": embedding,
                        "landmarks": list(map(list, landmarks)),
                        "quality_flags": raw.get("quality_flags", []),
                        "source": "face_embedding",
                        "confidence": quality,
                    },
                )
            )
    return records


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_face_coordinate(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _safe_face_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) != 4:
        return None
    if not all(_safe_face_coordinate(item) for item in value):
        return None
    return [float(item) for item in value]


def _coerce_face_model(result: dict[str, Any], configuration: dict[str, Any]) -> str:
    candidate = result.get("model")
    if isinstance(candidate, str) and candidate:
        return candidate
    face_config = configuration.get("face_embedding")
    if isinstance(face_config, dict):
        configured = face_config.get("model")
        if isinstance(configured, str) and configured:
            return configured
    return "sface"


def _coerce_embedding(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) > 512:
        return None
    output: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return None
        output.append(float(item))
    return output


def _maybe_embedding_from_sha256(value: Any) -> list[float] | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    return None


def _safe_json_list(value: Any, *, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:maximum]


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _close_locked_run_if_terminal(run: EventProcessingRun, now: timezone.datetime) -> None:
    from processing.services.reports import close_locked_run_report

    close_locked_run_report(run, now=now)
