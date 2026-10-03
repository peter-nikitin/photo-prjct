"""Bounded historical AdaFace enrollment with exact, private event reconciliation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from django.db import transaction
from django.db.models import Func, IntegerField, Prefetch
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventFaceClusterActivation,
    EventFaceEmbeddingActivation,
    EventProcessingRun,
    FaceEmbedding,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services import enrollment
from processing.services.face_quality import (
    _PUBLISHED_PREVIEW_KEY,
    _canonical_hash,
    historical_adaface_face_embedding_generations,
)

BACKFILL_REPORT_KEY = "historical_adaface_backfill"
ACTIVE_STATES = ("queued", "processing", "retry_wait")


def validate_backfill_receipt(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"cohort_sha256", "photo_count"}
        or not enrollment._is_sha256(value["cohort_sha256"])
        or isinstance(value["photo_count"], bool)
        or not isinstance(value["photo_count"], int)
        or value["photo_count"] < 1
    ):
        raise ValueError("invalid historical AdaFace cohort receipt")
    return value


def _inventory(event: Event) -> tuple[list[Photo], dict[str, dict | None], dict[str, Any]]:
    photos = list(Photo.objects.filter(event=event).select_related("upload_item").order_by("pk"))
    preview_states = dict(
        PhotoProcessingState.objects.filter(
            photo__event=event,
            processor_type="generate_preview",
            status="succeeded",
        ).values_list("photo_id", "accepted_attempt_id")
    )
    previews = {
        preview.photo_id: preview
        for preview in PhotoDerivative.objects.filter(
            photo__event=event,
            variant="preview-small-v1",
        )
        if preview_states.get(preview.photo_id) == preview.accepted_attempt_id
    }
    inputs: dict[str, dict | None] = {}
    snapshot = []
    for photo in photos:
        preview = previews.get(photo.pk)
        fingerprint = enrollment._derivative_fingerprint(preview) if preview else None
        valid = bool(
            enrollment._is_eligible(photo)
            and photo.original_size
            and preview
            and preview.accepted_attempt_id
            and preview.byte_size > 0
            and preview.width > 0
            and preview.height > 0
            and preview.content_type == "image/jpeg"
            and (key_match := _PUBLISHED_PREVIEW_KEY.fullmatch(preview.final_key))
            and key_match.group("photo_id") == photo.pk
        )
        inputs[photo.pk] = fingerprint if valid else None
        snapshot.append(
            {
                "photo": photo.pk,
                "original": enrollment._input_fingerprint(photo, verified_source_etag=None),
                "preview": fingerprint,
                "preview_sha256": preview.sha256 if preview else None,
                "preview_attempt": str(preview.accepted_attempt_id) if preview else None,
            }
        )
    receipt = {"cohort_sha256": _canonical_hash(snapshot), "photo_count": len(photos)}
    return photos, inputs, receipt


def _candidate_runs(event: Event, generation: dict) -> list[EventProcessingRun]:
    return list(
        EventProcessingRun.objects.filter(
            event=event,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
            configuration_hash=generation["configuration_hash"],
        ).order_by("created_at", "id")
    )


def _validate_frozen_receipts(runs: list[EventProcessingRun], receipt: dict) -> None:
    for run in runs:
        if validate_backfill_receipt(run.report.get(BACKFILL_REPORT_KEY)) != receipt:
            raise ValueError("historical AdaFace inventory or source changed")


def _job_identity(job: ProcessingJob, generation: dict, fingerprint: dict) -> bool:
    return (
        all(
            source.event_id == job.photo.event_id
            and source.contract_version == 3
            and source.processor_type == "face_embedding"
            and source.processor_version == 5
            and source.configuration == generation["configuration"]
            and source.configuration_hash == generation["configuration_hash"]
            for source in (job, job.run)
        )
        and job.input_fingerprint == fingerprint
    )


@dataclass
class _Evidence:
    states: dict[str, PhotoProcessingState]
    projections: set[tuple[str, UUID]]
    vectors: dict[UUID, list[tuple[UUID, str, int]]]
    json_attempts: set[UUID]
    old_searchable: set[str]


def _terminal_outcome(
    job: ProcessingJob, generation: dict, fingerprint: dict, evidence: _Evidence
) -> str:
    if not _job_identity(job, generation, fingerprint):
        return "evidence_blocker_count"
    if job.status in ACTIVE_STATES:
        return "pending_job_count"
    if job.status != ProcessingJob.Status.SUCCEEDED:
        return "failure_photo_count"
    attempts = [attempt for attempt in job.attempts.all() if attempt.accepted]
    if len(attempts) != 1:
        return "evidence_blocker_count"
    attempt = attempts[0]
    state = evidence.states.get(job.photo_id)
    if (
        attempt.status != "succeeded"
        or attempt.terminal_at is None
        or attempt.photo_id != job.photo_id
        or attempt.event_id != job.event_id
        or attempt.run_id != job.run_id
        or attempt.contract_version != 3
        or attempt.processor_type != "face_embedding"
        or attempt.processor_version != 5
        or attempt.configuration != job.configuration
        or attempt.input_fingerprint != fingerprint
        or state is None
        or state.status != "succeeded"
        or state.current_job_id != job.pk
        or state.accepted_attempt_id != attempt.pk
        or (job.photo_id, attempt.pk) not in evidence.projections
    ):
        return "evidence_blocker_count"
    artifacts = list(attempt.face_artifacts.all())
    if len(artifacts) != 1 or artifacts[0].status != "complete":
        return "evidence_blocker_count"
    detections = list(attempt.face_detections.all())
    kept = {d.pk for d in detections if d.status == "kept"}
    rejected = [d for d in detections if d.status == "quality_rejected"]
    vectors = evidence.vectors.get(attempt.pk, [])
    if (
        any(
            d.artifact_id != artifacts[0].pk or d.status not in ("kept", "quality_rejected")
            for d in detections
        )
        or {detection_id for detection_id, _, _ in vectors} != kept
        or any(
            model != generation["model"] or dimensions != 512 for _, model, dimensions in vectors
        )
        or attempt.pk in evidence.json_attempts
    ):
        return "evidence_blocker_count"
    if kept:
        return "accepted_photo_count"
    # Losing an already searchable photo requires action, never implicit successful deletion.
    if job.photo_id in evidence.old_searchable:
        return "replacement_blocker_count"
    return "quality_rejected_photo_count" if rejected else "no_face_photo_count"


def historical_adaface_status(event: Event) -> dict[str, Any]:
    generation = historical_adaface_face_embedding_generations()[0]
    photos, inputs, receipt = _inventory(event)
    runs = _candidate_runs(event, generation)
    _validate_frozen_receipts(runs, receipt)
    counts = Counter(
        {
            key: 0
            for key in (
                "not_enrolled_count",
                "source_blocker_count",
                "active_processing_count",
                "pending_job_count",
                "failure_photo_count",
                "evidence_blocker_count",
                "replacement_blocker_count",
                "accepted_photo_count",
                "no_face_photo_count",
                "quality_rejected_photo_count",
            )
        }
    )
    jobs = list(
        ProcessingJob.objects.filter(
            event=event,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
            configuration_hash=generation["configuration_hash"],
        )
        .select_related("run", "photo")
        .prefetch_related(
            Prefetch(
                "attempts",
                queryset=ProcessingAttempt.objects.only(
                    "id",
                    "job_id",
                    "accepted",
                    "status",
                    "terminal_at",
                    "photo_id",
                    "event_id",
                    "run_id",
                    "contract_version",
                    "processor_type",
                    "processor_version",
                    "configuration",
                    "input_fingerprint",
                ),
            ),
            Prefetch(
                "attempts__face_artifacts",
                queryset=FaceProcessingAttemptArtifact.objects.only("id", "attempt_id", "status"),
            ),
            Prefetch(
                "attempts__face_detections",
                queryset=PhotoFaceDetection.objects.only(
                    "id", "attempt_id", "artifact_id", "status"
                ),
            ),
        )
    )
    vectors: dict[UUID, list[tuple[UUID, str, int]]] = defaultdict(list)
    for attempt_id, detection_id, model, dimensions in (
        FaceEmbeddingVector.objects.filter(
            detection__attempt__event=event,
            detection__attempt__job__configuration_hash=generation["configuration_hash"],
        )
        .annotate(dimensions=Func("vector", function="vector_dims", output_field=IntegerField()))
        .values_list("detection__attempt_id", "detection_id", "model_version", "dimensions")
    ):
        vectors[attempt_id].append((detection_id, model, dimensions))
    evidence = _Evidence(
        states={
            state.photo_id: state
            for state in PhotoProcessingState.objects.filter(
                photo__event=event, processor_type="face_embedding"
            )
        },
        projections=set(
            PhotoFaceEmbeddingProjection.objects.filter(
                photo__event=event,
                contract_version=3,
                processor_version=5,
                configuration_hash=generation["configuration_hash"],
            ).values_list("photo_id", "accepted_attempt_id")
        ),
        vectors=vectors,
        json_attempts=set(
            FaceEmbedding.objects.filter(detection__attempt__event=event).values_list(
                "detection__attempt_id", flat=True
            )
        ),
        old_searchable=set(
            FaceEmbedding.objects.filter(
                detection__attempt__event=event,
                detection__status="kept",
                detection__attempt__accepted=True,
                model_version="sface",
            ).values_list("detection__attempt__photo_id", flat=True)
        ),
    )
    by_photo: dict[str, list[ProcessingJob]] = {}
    for job in jobs:
        by_photo.setdefault(job.photo_id, []).append(job)
    for photo in photos:
        fingerprint = inputs[photo.pk]
        selected = by_photo.get(photo.pk, [])
        if not selected:
            counts["not_enrolled_count"] += 1
        if fingerprint is None:
            counts["source_blocker_count"] += 1
        elif len(selected) > 1:
            counts["evidence_blocker_count"] += 1
        elif selected:
            counts[_terminal_outcome(selected[0], generation, fingerprint, evidence)] += 1
        else:
            if (
                state := evidence.states.get(photo.pk)
            ) is not None and state.status in ACTIVE_STATES:
                counts["active_processing_count"] += 1
            else:
                counts["paused_photo_count"] += 1
    counts["active_lease_count"] = ProcessingAttempt.objects.filter(
        event=event,
        processor_type="face_embedding",
        status="in_progress",
        lease_expires_at__gt=timezone.now(),
    ).count()
    # No IDs, source keys, embeddings or links leave this boundary.
    return {
        **receipt,
        **dict(counts),
        "paused_photo_count": counts["paused_photo_count"],
        "configuration_hash": generation["configuration_hash"],
    }


def enroll_historical_adaface(event: Event, *, cohort_sha256: str, limit: int) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        if event.face_search_generation != Event.FaceSearchGeneration.SFACE_V3:
            raise ValueError("historical enrollment requires an SFace event")
        before = historical_adaface_status(event)
        if any(
            before[key]
            for key in (
                "failure_photo_count",
                "evidence_blocker_count",
                "replacement_blocker_count",
            )
        ):
            raise ValueError(
                "historical AdaFace batch requires failure resolution before enrollment"
            )
        generation = historical_adaface_face_embedding_generations()[0]
        photos, inputs, receipt = _inventory(event)
        if receipt["cohort_sha256"] != cohort_sha256 or not photos:
            raise ValueError("explicit current cohort identity is required")
        _validate_frozen_receipts(_candidate_runs(event, generation), receipt)
        enrolled_ids = set(
            ProcessingJob.objects.filter(
                event=event,
                contract_version=3,
                processor_type="face_embedding",
                processor_version=5,
                configuration_hash=generation["configuration_hash"],
            ).values_list("photo_id", flat=True)
        )
        active_ids = set(
            PhotoProcessingState.objects.filter(
                photo__event=event, processor_type="face_embedding", status__in=ACTIVE_STATES
            ).values_list("photo_id", flat=True)
        )
        created = 0
        for photo in photos:
            fingerprint = inputs[photo.pk]
            if fingerprint is None:
                continue
            if photo.pk in enrolled_ids or photo.pk in active_ids:
                continue
            state = enrollment.request_processor(
                photo,
                processor_type="face_embedding",
                contract_version=3,
                processor_version=5,
                configuration=cast(dict[str, object], generation["configuration"]),
                input_fingerprint=fingerprint,
                replace_terminal_generation=True,
                event=event,
                historical_adaface_receipt=receipt,
            )
            job = state.current_job
            if job is None or not _job_identity(job, generation, fingerprint):
                raise ValueError("historical enrollment identity conflict")
            created += 1
            if created == limit:
                break
        return {**historical_adaface_status(event), "created_job_count": created}


def activate_historical_adaface(
    event: Event, *, cohort_sha256: str, quality_review_sha256: str, review_confirmed: bool
) -> dict[str, Any]:
    from selfie_search.models import SelfieSearch, SelfieSearchAttempt
    from selfie_search.services.read_selection import select_reader

    if review_confirmed is not True or not enrollment._is_sha256(quality_review_sha256):
        raise ValueError("explicit model quality review is required")
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        status = historical_adaface_status(event)
        if status["cohort_sha256"] != cohort_sha256 or not status["photo_count"]:
            raise ValueError("explicit current cohort identity is required")
        blockers = (
            "source_blocker_count",
            "active_processing_count",
            "not_enrolled_count",
            "pending_job_count",
            "failure_photo_count",
            "evidence_blocker_count",
            "replacement_blocker_count",
            "active_lease_count",
        )
        if any(status[key] for key in blockers):
            raise ValueError("incomplete historical AdaFace evidence")
        if (
            SelfieSearch.objects.filter(
                event=event, status__in=("queued", "processing", "cleanup_pending")
            ).exists()
            or SelfieSearchAttempt.objects.filter(
                job__search__event=event, status="in_progress", lease_expires_at__gt=timezone.now()
            ).exists()
        ):
            raise ValueError("event searches must drain before activation")
        generation = historical_adaface_face_embedding_generations()[0]
        generations = [generation]
        if any(
            select_reader(
                SelfieSearch(
                    configuration={"gallery_face_embedding_generations": generations},
                    reader_staff_eligible=staff,
                )
            )
            != "pgvector"
            for staff in (True, False)
        ):
            raise ValueError("historical AdaFace requires a safe native reader")
        latest = (
            EventFaceEmbeddingActivation.objects.filter(event=event)
            .order_by("-activated_at", "-id")
            .first()
        )
        if latest is None or latest.generations != generations:
            EventFaceEmbeddingActivation.objects.create(
                event=event,
                generations=generations,
                generation_set_hash=_canonical_hash(generations),
                approved_configuration_hash=generation["configuration_hash"],
                approved_evaluation_report_hash=quality_review_sha256,
            )
        EventFaceClusterActivation.objects.filter(event=event, active=True).update(
            active=False, deactivated_at=timezone.now()
        )
        event.face_search_generation = Event.FaceSearchGeneration.ADAFACE_V5
        event.save(update_fields=["face_search_generation"])
        return status
