"""Observe the publication and face-recognition paths of accepted originals."""

from datetime import datetime, timedelta

from config import queue_events
from django.db import connection, transaction
from django.db.models import Count, F, Min, Q
from picflow.gallery import gallery_media_policy_ready
from picflow.models import GalleryMediaProjection, Photo

from processing.models import (
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.face_cohort import (
    accepted_face_projections,
    current_accepted_face_result,
)

GALLERY_QUEUE = "photo_import_gallery"
FACE_QUEUE = "photo_import_face"

_MILLISECOND = timedelta(milliseconds=1)

_PREVIEW_POLICIES = (
    Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
    Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
)


def photo_import_backlogs() -> dict[str, tuple[int, int]]:
    """Return (unfinished photos, photos blocked by permanent failure), from one SQL snapshot."""
    # Non-null photo IDs let PostgreSQL hash each membership set once per observation.
    current_states = PhotoProcessingState.objects.filter(
        current_job__photo_id=F("photo_id"),
        current_job__processor_type=F("processor_type"),
    )
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
        pk__in=accepted_face_projections(include_hidden=True)
        .filter(
            current_accepted_face_result(),
            accepted_attempt__processor_type=FACE_EMBEDDING_PROCESSOR,
        )
        .values("photo_id"),
    )
    face_pending = (
        Q(original_content_type="image/jpeg")
        & (Q(gallery_media_policy__in=_PREVIEW_POLICIES) | face_requested)
        & ~face_complete
    )
    face_failed = Q(
        pk__in=failed_states.filter(processor_type=FACE_EMBEDDING_PROCESSOR).values("photo_id"),
    )

    counts = Photo.objects.filter(src="", original_key__gt="").aggregate(
        publication_pending=Count("pk", filter=publication_pending),
        publication_failed=Count(
            "pk", filter=publication_pending & (preview_failed | watermark_failed)
        ),
        face_pending=Count("pk", filter=face_pending),
        face_failed=Count("pk", filter=(face_failed | preview_failed) & face_pending),
    )
    return {
        GALLERY_QUEUE: (counts["publication_pending"], counts["publication_failed"]),
        FACE_QUEUE: (counts["face_pending"], counts["face_failed"]),
    }


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
