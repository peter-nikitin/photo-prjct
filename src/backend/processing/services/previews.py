"""Two-phase verified publication for worker-generated private preview derivatives."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ingestion.storage import ObjectChanged, ObjectMismatch, ObjectMissing
from picflow.models import Event, Photo

from processing.contracts import AttemptCompletion, CompletionConflict
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services import jobs
from processing.storage import ExactPreviewStorage, ObjectConflict, PreviewObject

_PREVIEW_WARNING_CODES = {"color_profile_missing"}
_PREVIEW_PHOTO_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class _PreviewPublication:
    staging_key: str
    final_key: str
    max_bytes: int
    max_width: int
    max_height: int
    result: dict[str, Any]


class _PreviewResultViolation(ValueError):
    """A returned preview result cannot be independently verified against the object contract."""


class _FinalPreviewConflict(CompletionConflict):
    """A final key exists but its immutable bytes are not this attempt's declared preview."""


class PreviewPublicationStorage(Protocol):
    def verify(self, *, key: str, max_bytes: int) -> PreviewObject: ...

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject: ...


def complete_preview_attempt(
    attempt_id: UUID,
    *,
    result: dict[str, Any],
    storage: PreviewPublicationStorage | None = None,
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
    clock: Callable[[], timezone.datetime] | None = None,
    jitter=None,
) -> AttemptCompletion:
    """Verify and promote outside database locks, then atomically accept one current attempt."""
    clock = clock or timezone.now
    payload = {
        "outcome": "success",
        "result": result,
        "download_duration_ms": download_duration_ms,
        "compute_duration_ms": compute_duration_ms,
        "total_duration_ms": total_duration_ms,
        "worker_started_at": worker_started_at,
        "worker_finished_at": worker_finished_at,
    }
    payload_hash = jobs._canonical_hash(payload)
    try:
        publication_or_completion = _preflight(
            attempt_id,
            payload=payload,
            payload_hash=payload_hash,
            clock=clock,
            jitter=jitter,
        )
    except _PreviewResultViolation:
        return _output_contract_failure(
            attempt_id,
            download_duration_ms=download_duration_ms,
            compute_duration_ms=compute_duration_ms,
            total_duration_ms=total_duration_ms,
            worker_started_at=worker_started_at,
            worker_finished_at=worker_finished_at,
            clock=clock,
            jitter=jitter,
        )
    if isinstance(publication_or_completion, AttemptCompletion):
        return publication_or_completion
    publication = publication_or_completion
    storage = storage or ExactPreviewStorage()

    try:
        _verify_and_promote(storage, publication)
    except _PreviewResultViolation:
        return _output_contract_failure(
            attempt_id,
            download_duration_ms=download_duration_ms,
            compute_duration_ms=compute_duration_ms,
            total_duration_ms=total_duration_ms,
            worker_started_at=worker_started_at,
            worker_finished_at=worker_finished_at,
            clock=clock,
            jitter=jitter,
        )
    except (ObjectMismatch, ObjectChanged, ObjectMissing):
        return _output_contract_failure(
            attempt_id,
            download_duration_ms=download_duration_ms,
            compute_duration_ms=compute_duration_ms,
            total_duration_ms=total_duration_ms,
            worker_started_at=worker_started_at,
            worker_finished_at=worker_finished_at,
            clock=clock,
            jitter=jitter,
        )

    return _publish_after_verification(
        attempt_id,
        publication=publication,
        payload=payload,
        payload_hash=payload_hash,
        clock=clock,
        jitter=jitter,
    )


def _output_contract_failure(
    attempt_id: UUID,
    *,
    download_duration_ms: int | None,
    compute_duration_ms: int | None,
    total_duration_ms: int | None,
    worker_started_at: str | None,
    worker_finished_at: str | None,
    clock: Callable[[], timezone.datetime],
    jitter,
) -> AttemptCompletion:
    return jobs.fail_attempt(
        attempt_id,
        error_code="output_contract_violation",
        error_detail="",
        retryable=False,
        download_duration_ms=download_duration_ms,
        compute_duration_ms=compute_duration_ms,
        total_duration_ms=total_duration_ms,
        worker_started_at=worker_started_at,
        worker_finished_at=worker_finished_at,
        jitter=jitter,
    )


def _preflight(
    attempt_id: UUID,
    *,
    payload: dict[str, Any],
    payload_hash: str,
    clock: Callable[[], timezone.datetime],
    jitter,
) -> _PreviewPublication | AttemptCompletion:
    """Lock only long enough to establish that storage work belongs to the current lease."""
    with transaction.atomic():
        _, run, job, _, state, attempt = jobs._locked_context(attempt_id)
        now = clock()
        _require_preview_attempt(job, attempt)
        if attempt.status != ProcessingAttempt.Status.IN_PROGRESS:
            return jobs._duplicate(attempt, payload_hash)
        if not jobs._owns_current_lease(state, attempt, now):
            return _record_not_current(
                run,
                job,
                state,
                attempt,
                payload=payload,
                payload_hash=payload_hash,
                now=now,
                jitter=jitter,
            )
        return _publication_for(job, attempt, result=payload["result"])


def _publish_after_verification(
    attempt_id: UUID,
    *,
    publication: _PreviewPublication,
    payload: dict[str, Any],
    payload_hash: str,
    clock: Callable[[], timezone.datetime],
    jitter,
) -> AttemptCompletion:
    """Re-lock the run-to-attempt chain and make the derivative/state transition indivisible."""
    with transaction.atomic():
        _prelock_preview_face_enrollment(attempt_id)
        _, run, job, photo, state, attempt = jobs._locked_context(attempt_id)
        now = clock()
        _require_preview_attempt(job, attempt)
        if attempt.status != ProcessingAttempt.Status.IN_PROGRESS:
            return jobs._duplicate(attempt, payload_hash)
        if not jobs._owns_current_lease(state, attempt, now):
            return _record_not_current(
                run,
                job,
                state,
                attempt,
                payload=payload,
                payload_hash=payload_hash,
                now=now,
                jitter=jitter,
            )
        existing = (
            PhotoDerivative.objects.select_for_update()
            .filter(photo_id=attempt.photo_id, variant=str(publication.result["variant"]))
            .first()
        )
        if existing is not None:
            raise CompletionConflict(
                "A preview derivative was already published for this photo.",
                attempt_id=attempt.id,
                submitted_hash=payload_hash,
            )
        jobs._terminal_success(attempt, payload, now)
        job.status = ProcessingJob.Status.SUCCEEDED
        job.completed_at = now
        job.save(update_fields=["status", "completed_at"])
        jobs._set_state(
            state,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=now,
            next_attempt_at=None,
        )
        PhotoDerivative.objects.create(
            photo_id=attempt.photo_id,
            variant=publication.result["variant"],
            final_key=publication.final_key,
            byte_size=publication.result["byte_size"],
            content_type=publication.result["content_type"],
            width=publication.result["width"],
            height=publication.result["height"],
            oriented_source_width=publication.result["oriented_source_width"],
            oriented_source_height=publication.result["oriented_source_height"],
            sha256=publication.result["sha256"],
            accepted_attempt=attempt,
        )
        # The only automatic edge into preview-backed ML is this accepted, published transition.
        # Its rows were locked before the preview Attempt, so an enqueue failure rolls this whole
        # acceptance transaction back without a leftward lock acquisition.
        from processing.services.enrollment import request_face_embedding_enqueue

        request_face_embedding_enqueue(photo)
        jobs._close_locked_run_if_terminal(run, now)
        return AttemptCompletion(attempt=attempt)


def _prelock_preview_face_enrollment(attempt_id: UUID) -> None:
    """Lock the optional preview-backed face enrollment path before the preview Attempt."""
    identity = ProcessingAttempt.objects.only("event_id", "photo_id").get(pk=attempt_id)
    event = Event.objects.select_for_update().get(pk=identity.event_id)
    generation = Photo.objects.values_list("processing_generation", flat=True).get(
        pk=identity.photo_id
    )
    if generation == Photo.ProcessingGeneration.PREVIEW_FIRST_V1 and bool(
        getattr(settings, "PHOTO_PROCESSING_FACE_ENABLED", False)
    ):
        from processing.services.enrollment import (
            LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION,
            LOCAL_ADAFACE_QUALITY_FACE_PROCESSOR_VERSION,
            PREVIEW_CONTRACT_VERSION,
            PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
            QUALITY_FACE_CONTRACT_VERSION,
            SCRFD_FACE_EMBEDDING_CONFIGURATION,
            _configuration_hash,
        )

        if event.face_search_generation == Event.FaceSearchGeneration.ADAFACE_V5:
            contract_version = QUALITY_FACE_CONTRACT_VERSION
            processor_version = LOCAL_ADAFACE_QUALITY_FACE_PROCESSOR_VERSION
            configuration = LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION
        else:
            contract_version = PREVIEW_CONTRACT_VERSION
            processor_version = PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION
            configuration = SCRFD_FACE_EMBEDDING_CONFIGURATION
        configuration_hash = _configuration_hash(configuration)
        runs = EventProcessingRun.objects.select_for_update().filter(
            event=event,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration_hash=configuration_hash,
            status=EventProcessingRun.Status.COLLECTING,
        )
        for run in runs:
            list(
                ProcessingJob.objects.select_for_update()
                .filter(run=run, photo_id=identity.photo_id)
                .order_by("id")
            )
    photo = Photo.objects.select_for_update().get(pk=identity.photo_id)
    PhotoProcessingState.objects.select_for_update().get_or_create(
        photo=photo,
        processor_type="face_embedding",
        defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
    )


def _record_not_current(
    run: EventProcessingRun,
    job: ProcessingJob,
    state: PhotoProcessingState,
    attempt: ProcessingAttempt,
    *,
    payload: dict[str, Any],
    payload_hash: str,
    now: timezone.datetime,
    jitter,
) -> AttemptCompletion:
    if attempt.lease_expires_at is not None and attempt.lease_expires_at <= now:
        jobs._recover_expired(run, job, state, attempt, now=now, jitter=jitter)
        return jobs._record_late_receipt(attempt, payload, payload_hash, now)
    jobs._terminal_stale(attempt, payload, payload_hash, now)
    return AttemptCompletion(attempt=attempt, stale=True)


def _publication_for(
    job: ProcessingJob, attempt: ProcessingAttempt, *, result: dict[str, Any]
) -> _PreviewPublication:
    config = job.configuration.get("generate_preview")
    if not isinstance(config, dict):
        raise _PreviewResultViolation()
    variant = config.get("variant")
    max_bytes = config.get("max_output_bytes")
    max_width = config.get("max_output_width")
    max_height = config.get("max_output_height")
    if not (
        variant == "preview-small-v1"
        and isinstance(max_bytes, int)
        and not isinstance(max_bytes, bool)
        and max_bytes > 0
        and isinstance(max_width, int)
        and not isinstance(max_width, bool)
        and max_width > 0
        and isinstance(max_height, int)
        and not isinstance(max_height, bool)
        and max_height > 0
        and config.get("output_format") == "jpeg"
        and config.get("checksum_algorithm") == "sha256"
    ):
        raise _PreviewResultViolation()
    validated_result = _validated_result(
        result,
        variant=variant,
        max_bytes=max_bytes,
        max_width=max_width,
        max_height=max_height,
    )
    fingerprint = job.input_fingerprint
    source_width = fingerprint.get("pixel_width")
    source_height = fingerprint.get("pixel_height")
    if not (
        isinstance(source_width, int)
        and not isinstance(source_width, bool)
        and source_width > 0
        and isinstance(source_height, int)
        and not isinstance(source_height, bool)
        and source_height > 0
        and validated_result["oriented_source_width"] == source_width
        and validated_result["oriented_source_height"] == source_height
    ):
        raise _PreviewResultViolation()
    return _PreviewPublication(
        staging_key=(f"processing-pending/previews/{attempt.id}/preview-small-v1.jpg"),
        final_key=preview_final_key(
            photo_id=attempt.photo_id,
            attempt_id=attempt.id,
            sha256=validated_result["sha256"],
        ),
        max_bytes=max_bytes,
        max_width=max_width,
        max_height=max_height,
        result=validated_result,
    )


def _validated_result(
    result: dict[str, Any],
    *,
    variant: str,
    max_bytes: int,
    max_width: int,
    max_height: int,
) -> dict[str, Any]:
    required = {
        "variant",
        "content_type",
        "byte_size",
        "width",
        "height",
        "oriented_source_width",
        "oriented_source_height",
        "sha256",
        "upload_ms",
        "warnings",
    }
    if not isinstance(result, dict) or set(result) != required:
        raise _PreviewResultViolation()
    if result.get("variant") != variant or result.get("content_type") != "image/jpeg":
        raise _PreviewResultViolation()
    if not all(
        isinstance(result.get(name), int)
        and not isinstance(result.get(name), bool)
        and result[name] > 0
        for name in (
            "byte_size",
            "width",
            "height",
            "oriented_source_width",
            "oriented_source_height",
        )
    ):
        raise _PreviewResultViolation()
    if (
        result["byte_size"] > max_bytes
        or result["width"] > max_width
        or result["height"] > max_height
        or not isinstance(result.get("sha256"), str)
        or len(result["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in result["sha256"])
        or not isinstance(result.get("upload_ms"), int)
        or isinstance(result.get("upload_ms"), bool)
        or result["upload_ms"] < 0
        or result["upload_ms"] > 86_400_000
        or not isinstance(result.get("warnings"), list)
        or len(result["warnings"]) > 8
        or any(code not in _PREVIEW_WARNING_CODES for code in result["warnings"])
    ):
        raise _PreviewResultViolation()
    return result


def preview_final_key(*, photo_id: str, attempt_id: UUID, sha256: str) -> str:
    """Return the immutable content-addressed preview identity selected by Django."""
    if _PREVIEW_PHOTO_ID.fullmatch(photo_id) is None or _SHA256.fullmatch(sha256) is None:
        raise ValueError("invalid preview final identity")
    return f"derivatives/previews/{photo_id}/preview-small-v1/{attempt_id}-{sha256}.jpg"


def _verify_and_promote(
    storage: PreviewPublicationStorage, publication: _PreviewPublication
) -> None:
    staging = storage.verify(key=publication.staging_key, max_bytes=publication.max_bytes)
    _assert_matches_result(staging, publication)
    try:
        final = storage.verify(key=publication.final_key, max_bytes=publication.max_bytes)
    except ObjectMissing:
        try:
            storage.promote(
                staging_key=publication.staging_key,
                final_key=publication.final_key,
                source_etag=staging.etag_wire,
            )
        except (ObjectConflict, ObjectChanged):
            # A copy may have succeeded immediately before a process interruption or a competing
            # request.  Only a full verification of that immutable final object may converge.
            pass
        final = storage.verify(key=publication.final_key, max_bytes=publication.max_bytes)
    _assert_matches_result(final, publication, conflicting_final=True)


def _assert_matches_result(
    object: PreviewObject, publication: _PreviewPublication, *, conflicting_final: bool = False
) -> None:
    result = publication.result
    if (
        object.byte_size != result["byte_size"]
        or object.content_type != result["content_type"]
        or object.sha256 != result["sha256"]
        or object.width != result["width"]
        or object.height != result["height"]
    ):
        if conflicting_final:
            raise _FinalPreviewConflict("An existing final preview does not match this completion.")
        raise _PreviewResultViolation()


def _require_preview_attempt(job: ProcessingJob, attempt: ProcessingAttempt) -> None:
    if (
        job.processor_type != GENERATE_PREVIEW_PROCESSOR
        or attempt.processor_type != GENERATE_PREVIEW_PROCESSOR
    ):
        raise ValueError("Preview publication requires a generate_preview attempt.")
