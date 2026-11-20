"""Observe the publication and face-recognition paths of accepted originals."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from config import queue_events
from config.queue_telemetry_protocol import FACE_QUEUE, GALLERY_QUEUE
from django.db import connection, transaction
from django.db.backends.utils import CursorWrapper
from django.db.models import F, Min, Q
from picflow.gallery import gallery_media_policy_ready
from picflow.models import GalleryMediaProjection, Photo

from processing.models import (
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoImportCounter,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.face_cohort import (
    accepted_face_projections,
    current_accepted_face_result,
)

logger = logging.getLogger(__name__)

_MILLISECOND = timedelta(milliseconds=1)
REBUILD_PAGE_SIZE = 25

type PhotoImportBacklogs = dict[str, tuple[int, int]]

_PREVIEW_POLICIES = (
    Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
    Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
)


_BACKLOG_PROCESSORS = (
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
)


def _point_photo_import_backlogs(photo_id: str) -> PhotoImportBacklogs:
    """Observe a locked photo's membership with one scalar SELECT."""
    current_states = PhotoProcessingState.objects.filter(
        photo_id=photo_id,
        current_job__photo_id=F("photo_id"),
        current_job__processor_type=F("processor_type"),
    )
    photos = Photo.objects.filter(pk=photo_id, src="", original_key__gt="")
    face_projections = accepted_face_projections(include_hidden=True)
    failed_states = current_states.filter(
        status=PhotoProcessingState.Status.FAILED,
        current_job__status=ProcessingJob.Status.FAILED,
    )

    clean_not_published = Q(gallery_media_projection__clean_preview_final_key__isnull=True)
    watermarked_policy = Q(
        gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
    )
    preview_failed = clean_not_published & Q(
        pk__in=failed_states.filter(processor_type=GENERATE_PREVIEW_PROCESSOR).values("photo_id")
    )
    watermark_failed = (
        watermarked_policy
        & ~clean_not_published
        & Q(
            pk__in=failed_states.filter(
                processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR
            ).values("photo_id")
        )
    )

    publication_pending = ~gallery_media_policy_ready()
    face_requested = Q(
        pk__in=current_states.filter(processor_type=FACE_EMBEDDING_PROCESSOR)
        .exclude(status=PhotoProcessingState.Status.CANCELLED)
        .values("photo_id")
    )
    face_complete = Q(
        pk__in=face_projections.filter(
            current_accepted_face_result(),
            photo_id=photo_id,
            photo__processing_states__photo_id=photo_id,
            accepted_attempt__processor_type=FACE_EMBEDDING_PROCESSOR,
        ).values("photo_id"),
    )
    face_pending = (
        Q(original_content_type="image/jpeg")
        & (Q(gallery_media_policy__in=_PREVIEW_POLICIES) | face_requested)
        & ~face_complete
    )
    face_failed = Q(
        pk__in=failed_states.filter(processor_type=FACE_EMBEDDING_PROCESSOR).values("photo_id"),
    )

    membership = {
        "publication_pending": publication_pending,
        "publication_failed": publication_pending & (preview_failed | watermark_failed),
        "face_pending": face_pending,
        "face_failed": (face_failed | preview_failed) & face_pending,
    }
    counts = photos.annotate(**membership).values(*membership).first() or dict.fromkeys(
        membership, 0
    )
    return {
        GALLERY_QUEUE: (int(counts["publication_pending"]), int(counts["publication_failed"])),
        FACE_QUEUE: (int(counts["face_pending"]), int(counts["face_failed"])),
    }


def _batch_accepted_face_ids(photo_ids: Sequence[str], states: list[dict[str, Any]]) -> set[str]:
    current_results = {
        (
            state["photo_id"],
            state["accepted_attempt_id"],
            state["current_job_id"],
            state["current_run_id"],
        )
        for state in states
        if state["processor_type"] == FACE_EMBEDDING_PROCESSOR
        and state["status"] == PhotoProcessingState.Status.SUCCEEDED
        and state["accepted_attempt_id"] is not None
        and state["accepted_attempt_id"] == state["current_attempt_id"]
    }
    projections = (
        accepted_face_projections(include_hidden=True)
        .filter(
            photo_id__in=photo_ids,
            accepted_attempt_id__in={result[1] for result in current_results},
            accepted_attempt__processor_type=FACE_EMBEDDING_PROCESSOR,
        )
        .values_list(
            "photo_id",
            "accepted_attempt_id",
            "accepted_attempt__job_id",
            "accepted_attempt__run_id",
        )
    )
    return {result[0] for result in projections if result in current_results}


def photo_import_backlogs(photo_ids: Sequence[str]) -> PhotoImportBacklogs:
    """Read only page photos and the identities referenced by their current states."""
    if len(photo_ids) > REBUILD_PAGE_SIZE:
        raise ValueError("Photo import backlog reads require at most one page")
    photos = list(
        Photo.objects.filter(pk__in=photo_ids, src="", original_key__gt="")
        .order_by()
        .annotate(
            gallery_ready=gallery_media_policy_ready(),
            clean_ready=Q(gallery_media_projection__clean_preview_final_key__isnull=False),
        )
        .values(
            "pk", "gallery_media_policy", "original_content_type", "gallery_ready", "clean_ready"
        )
    )
    accepted_photo_ids = [photo["pk"] for photo in photos]
    states = list(
        PhotoProcessingState.objects.filter(
            photo_id__in=accepted_photo_ids, processor_type__in=_BACKLOG_PROCESSORS
        ).values(
            "photo_id",
            "processor_type",
            "status",
            "current_job_id",
            "current_run_id",
            "current_attempt_id",
            "accepted_attempt_id",
        )
    )
    accepted_faces = _batch_accepted_face_ids(accepted_photo_ids, states)
    photos_requiring_enrollment_check = {
        photo["pk"]
        for photo in photos
        if photo["original_content_type"] == "image/jpeg"
        and photo["gallery_media_policy"] not in _PREVIEW_POLICIES
        and photo["pk"] not in accepted_faces
    }
    current_job_ids = {
        state["current_job_id"]
        for state in states
        if state["current_job_id"] is not None
        and (
            state["status"] == PhotoProcessingState.Status.FAILED
            or (
                state["processor_type"] == FACE_EMBEDDING_PROCESSOR
                and state["photo_id"] in photos_requiring_enrollment_check
                and state["status"] != PhotoProcessingState.Status.CANCELLED
            )
        )
    }
    jobs = {
        job_id: (photo_id, processor, status)
        for job_id, photo_id, processor, status in ProcessingJob.objects.filter(
            pk__in=current_job_ids
        ).values_list("pk", "photo_id", "processor_type", "status")
    }
    requested_faces: set[str] = set()
    failed_stages: set[tuple[str, str]] = set()
    for state in states:
        photo_id, processor = state["photo_id"], state["processor_type"]
        job = jobs.get(state["current_job_id"])
        if job is None or (job[0], job[1]) != (photo_id, processor):
            continue
        if (
            processor == FACE_EMBEDDING_PROCESSOR
            and state["status"] != PhotoProcessingState.Status.CANCELLED
        ):
            requested_faces.add(photo_id)
        if (
            state["status"] == PhotoProcessingState.Status.FAILED
            and job[2] == ProcessingJob.Status.FAILED
        ):
            failed_stages.add((photo_id, processor))
    gallery_pending = gallery_failed = face_pending = face_failed = 0
    for photo in photos:
        photo_id = photo["pk"]
        policy = photo["gallery_media_policy"]
        preview_failed = (
            not photo["clean_ready"] and (photo_id, GENERATE_PREVIEW_PROCESSOR) in failed_stages
        )
        if not photo["gallery_ready"]:
            gallery_pending += 1
            watermark_failed = (
                photo["clean_ready"]
                and policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
                and (photo_id, GENERATE_WATERMARKED_PREVIEW_PROCESSOR) in failed_stages
            )
            gallery_failed += preview_failed or watermark_failed
        if (
            photo["original_content_type"] == "image/jpeg"
            and (policy in _PREVIEW_POLICIES or photo_id in requested_faces)
            and photo_id not in accepted_faces
        ):
            face_pending += 1
            face_failed += preview_failed or (photo_id, FACE_EMBEDDING_PROCESSOR) in failed_stages
    return {
        GALLERY_QUEUE: (gallery_pending, gallery_failed),
        FACE_QUEUE: (face_pending, face_failed),
    }


@contextmanager
def _rebuild_read() -> Iterator[CursorWrapper]:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = 250")
        yield cursor


def read_photo_import_backlog_upper_bound() -> str | None:
    """Fence the pass at the largest photo ID visible at its start."""
    # Pattern-ops comparisons use the character PK index even under a non-binary locale.
    with _rebuild_read() as cursor:
        cursor.execute("SELECT id FROM picflow_photo ORDER BY id USING ~>~ LIMIT 1")
        row = cursor.fetchone()
    return row[0] if row is not None else None


def read_photo_import_backlog_page(
    *, after: str | None, upper_bound: str
) -> tuple[str, PhotoImportBacklogs] | None:
    """Read no more than one page of IDs and their bounded current memberships."""
    sql = "SELECT id FROM picflow_photo WHERE id ~<=~ %s"
    params: list[str | int] = [upper_bound]
    if after is not None:
        sql += " AND id ~>~ %s"
        params.append(after)
    sql += " ORDER BY id USING ~<~ LIMIT %s"
    params.append(REBUILD_PAGE_SIZE)
    with _rebuild_read() as cursor:
        cursor.execute(sql, params)
        photo_ids = [row[0] for row in cursor.fetchall()]
        if not photo_ids:
            return None
        return photo_ids[-1], photo_import_backlogs(photo_ids)


def _read_single_photo_backlogs(photo_id: str) -> PhotoImportBacklogs | None:
    """Isolate best-effort reads from the business transaction using a savepoint."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('statement_timeout')")
            previous_timeout = cursor.fetchone()[0]
            cursor.execute("SET LOCAL statement_timeout = 100")
            counts = _point_photo_import_backlogs(photo_id)
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", [previous_timeout])
    except Exception:
        logger.warning("Could not observe photo import backlog")
        return None
    return counts


def schedule_original_accepted(photo: Photo) -> None:
    """An accepted preview-first original enters both paths before processing."""
    if photo.gallery_media_policy in _PREVIEW_POLICIES:
        deltas = {GALLERY_QUEUE: (1, 0)}
        if photo.original_content_type == "image/jpeg":
            deltas[FACE_QUEUE] = (1, 0)
        _schedule_backlog_delta(deltas)


def capture_photo_import_backlog(photo_id: str, processor_type: str) -> PhotoImportBacklogs | None:
    if processor_type not in _BACKLOG_PROCESSORS:
        return None
    return _read_single_photo_backlogs(photo_id)


def schedule_photo_import_backlog_delta(photo_id: str, before: PhotoImportBacklogs | None) -> None:
    if before is None:
        return
    after = _read_single_photo_backlogs(photo_id)
    if after is None:
        return
    deltas = {}
    for queue, (pending, failed) in after.items():
        previous_pending, previous_failed = before[queue]
        delta = (pending - previous_pending, failed - previous_failed)
        if delta != (0, 0):
            deltas[queue] = delta
    if deltas:
        _schedule_backlog_delta(deltas)


def _schedule_backlog_delta(deltas: PhotoImportBacklogs) -> None:
    transaction.on_commit(lambda: _apply_backlog_delta(deltas), robust=True)


def _apply_backlog_delta(deltas: PhotoImportBacklogs) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 100")
        cursor.execute("SET LOCAL lock_timeout = 10")
        for queue, (pending, failed) in sorted(deltas.items()):
            PhotoImportCounter.objects.filter(pk=queue).update(
                pending=F("pending") + pending, failed=F("failed") + failed
            )


def schedule_photo_import_completion(photo: Photo, attempt: ProcessingAttempt) -> None:
    """Schedule the observation at the product transaction's acceptance boundary."""
    processor = attempt.processor_type
    policy = photo.gallery_media_policy
    if photo.uploaded_at is None or policy not in _PREVIEW_POLICIES:
        return
    if (
        processor == FACE_EMBEDDING_PROCESSOR
        or (
            processor == GENERATE_PREVIEW_PROCESSOR
            and policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        )
        or (
            processor == GENERATE_WATERMARKED_PREVIEW_PROCESSOR
            and policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        )
    ):
        transaction.on_commit(lambda: _observe_completion(photo, attempt), robust=True)


def _observe_completion(photo: Photo, attempt: ProcessingAttempt) -> None:
    is_face = attempt.processor_type == FACE_EMBEDDING_PROCESSOR
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 100")
        elapsed = _completion_elapsed(photo, attempt, is_face=is_face)
    if elapsed is None:
        return
    downstream = "face" if is_face else "watermark"
    names = ("preview_wait", "preview_elapsed", f"{downstream}_wait", f"{downstream}_elapsed")
    queue_events.notify_queue_completion(
        FACE_QUEUE if is_face else GALLERY_QUEUE, dict(zip(names, elapsed, strict=True))
    )


def _completion_elapsed(
    photo: Photo, attempt: ProcessingAttempt, *, is_face: bool
) -> list[int] | None:
    if attempt.terminal_at is None:
        return None
    needs_preview = attempt.processor_type != GENERATE_PREVIEW_PROCESSOR
    if (
        is_face
        and ProcessingAttempt.objects.filter(
            photo_id=photo.pk,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            accepted=True,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at__lte=attempt.terminal_at,
        )
        .exclude(pk=attempt.pk)
        .exists()
    ):
        return None

    preview_job_id, preview_terminal_at = attempt.job_id, attempt.terminal_at
    if needs_preview:
        preview = (
            GalleryMediaProjection.objects.filter(photo_id=photo.pk)
            .values_list(
                "clean_preview_source_attempt__job_id",
                "clean_preview_source_attempt__terminal_at",
            )
            .first()
        )
        if preview is None:
            return None
        preview_job_id, preview_terminal_at = preview

    first_claims = dict(
        ProcessingAttempt.objects.filter(job_id__in=(preview_job_id, attempt.job_id))
        .values("job_id")
        .annotate(first=Min("claimed_at"))
        .values_list("job_id", "first")
    )
    downstream_claimed_at = (
        first_claims.get(attempt.job_id) if needs_preview else preview_terminal_at
    )
    return _elapsed_ms(
        photo.uploaded_at,
        first_claims.get(preview_job_id),
        preview_terminal_at,
        downstream_claimed_at,
        attempt.terminal_at,
    )


def _elapsed_ms(start: datetime | None, *ends: datetime | None) -> list[int] | None:
    """Difference rounded cumulative boundaries so every millisecond is accounted for once."""
    if start is None:
        return None
    previous = start
    previous_ms = 0
    elapsed = []
    for end in ends:
        if end is None or end < previous:
            return None
        end_ms = (end - start) // _MILLISECOND
        elapsed.append(end_ms - previous_ms)
        previous, previous_ms = end, end_ms
    return elapsed
