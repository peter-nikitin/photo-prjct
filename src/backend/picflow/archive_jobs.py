from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import timedelta
from functools import partial
from hashlib import sha256
from shutil import disk_usage
from tempfile import TemporaryFile, gettempdir
from threading import Condition
from time import monotonic, monotonic_ns
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from config.queue_notifications import ARCHIVE_CHANNEL, notify_work
from config.queue_timings import elapsed_ms
from config.queue_work import ARCHIVE, schedule_transition
from django.db import connection, transaction
from django.utils import timezone
from ingestion.storage import (
    PrivateUploadStorage,
    StorageUnavailable,
    validate_archive_filename,
    validate_original_key,
)

from picflow.archive import (
    ArchiveEntry,
    ArchiveObservation,
    ArchiveSourceMissing,
    ArchiveSourceUnavailable,
    prepare_zip_archive,
    validate_archive_entries,
)
from picflow.gallery_image_delivery import ARCHIVE_DOWNLOAD_TTL_SECONDS, sign_private_archive
from picflow.models import PhotoArchiveJob

logger = logging.getLogger(__name__)
_RETENTION = timedelta(hours=24)
_MAX_PAGE_ENTRIES = 100
_MAX_ENTRY_BYTES = 50 * 1024 * 1024
_DISK_MARGIN_BYTES = 1024 * 1024 * 1024
_space_condition = Condition()
_reserved_archive_bytes = 0


@contextmanager
def _reserve_archive_space(entries: Sequence[dict]) -> Iterator[int]:
    global _reserved_archive_bytes
    # ZIP_STORED retains source bytes; allow ZIP64 headers and generous filename overhead.
    required = sum(entry["original_size"] + 4096 for entry in entries) + 65536
    started = monotonic()
    with _space_condition:
        while (
            required + _reserved_archive_bytes + _DISK_MARGIN_BYTES > disk_usage(gettempdir()).free
        ):
            if not _reserved_archive_bytes:
                raise OSError("insufficient temporary archive space")
            _space_condition.wait()
        # Written bytes also reduce free space, making this reservation conservative.
        _reserved_archive_bytes += required
    try:
        yield round((monotonic() - started) * 1000)
    finally:
        with _space_condition:
            _reserved_archive_bytes -= required
            _space_condition.notify_all()


def enqueue_archive_job(
    *,
    scope: str,
    entries: Sequence[ArchiveEntry],
    filename: str,
    observation: ArchiveObservation,
    order_item_ids: Sequence[int] = (),
) -> PhotoArchiveJob:
    """Enqueue or reuse work for a freshly authorized collection of originals."""
    if observation.context != "free_result" and len(entries) > _MAX_PAGE_ENTRIES:
        raise ValueError("archive exceeds the page limit")
    validate_archive_entries(tuple(entries))
    validate_archive_filename(filename)
    if observation.context == "paid_order":
        if len(order_item_ids) != len(entries) or any(
            type(item_id) is not int or item_id < 1 for item_id in order_item_ids
        ):
            raise ValueError("paid archive needs a matching order item for every photo")
    elif order_item_ids:
        raise ValueError("free archive cannot contain order items")

    payload = []
    for index, entry in enumerate(entries):
        validate_original_key(entry.original_key)
        if entry.original_size > _MAX_ENTRY_BYTES:
            raise ValueError("archive entry exceeds the original size limit")
        payload.append(
            {
                "photo_id": entry.photo_id,
                "original_key": entry.original_key,
                "original_size": entry.original_size,
                "original_content_type": entry.original_content_type,
                "order_item_id": order_item_ids[index] if order_item_ids else None,
            }
        )
    identity = [observation.context, scope, observation.page, filename, payload]
    request_key = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    with transaction.atomic():
        queued_at = timezone.now()
        job, created = PhotoArchiveJob.objects.select_for_update().get_or_create(
            request_key=request_key,
            defaults={
                "entries": payload,
                "filename": filename,
                "context": observation.context,
                "page": observation.page,
                "queue_enqueued_at": queued_at,
            },
        )
        previous_status = None if created else job.status
        if job.status == PhotoArchiveJob.Status.FAILED or _expired(job):
            job.status = PhotoArchiveJob.Status.QUEUED
            job.public_id = uuid4()
            job.expires_at = None
            job.queue_enqueued_at = queued_at
            job.queue_first_claimed_at = None
            job.save(
                update_fields=[
                    "status",
                    "public_id",
                    "expires_at",
                    "updated_at",
                    "queue_enqueued_at",
                    "queue_first_claimed_at",
                ]
            )
        schedule_transition(ARCHIVE, previous_status, job.status)
        if job.status == PhotoArchiveJob.Status.QUEUED:
            notify_work(ARCHIVE_CHANNEL)
    return job


def find_archive_job(public_id: UUID) -> PhotoArchiveJob | None:
    """Look up the archive whose opaque link was issued at preparation time."""
    job = PhotoArchiveJob.objects.filter(public_id=public_id).first()
    return None if job is not None and _expired(job) else job


def sign_archive_job(job: PhotoArchiveJob) -> str:
    if job.status != PhotoArchiveJob.Status.READY or job.expires_at is None:
        raise StorageUnavailable()
    expires_in = min(
        ARCHIVE_DOWNLOAD_TTL_SECONDS, int((job.expires_at - timezone.now()).total_seconds())
    )
    if expires_in < 1:
        raise StorageUnavailable()
    return sign_private_archive(object_id=job.object_id, expires_in=expires_in)


@transaction.atomic
def recover_interrupted_archives() -> None:
    """Requeue work abandoned by the previous holder of the worker lock."""
    updated = PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.RUNNING).update(
        status=PhotoArchiveJob.Status.QUEUED,
        updated_at=timezone.now(),
    )
    schedule_transition(
        ARCHIVE,
        str(PhotoArchiveJob.Status.RUNNING),
        str(PhotoArchiveJob.Status.QUEUED),
        amount=updated,
    )

    if updated:
        notify_work(ARCHIVE_CHANNEL)


def prune_archive_jobs() -> None:
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH pruned AS (
                DELETE FROM picflow_photoarchivejob
                WHERE (status = 'ready' AND expires_at <= %s)
                   OR (status IN ('failed', 'queued') AND updated_at <= %s)
                RETURNING status
            )
            SELECT count(*) FROM pruned WHERE status = 'queued'
            """,
            [now, now - _RETENTION],
        )
        removed = cursor.fetchone()
    if removed is not None:
        schedule_transition(
            ARCHIVE, str(PhotoArchiveJob.Status.QUEUED), None, amount=removed[0], outcome="canceled"
        )


def has_queued_archives() -> bool:
    """Include committed queued rows temporarily skipped by a competing row lock."""
    return PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.QUEUED).exists()


def process_next_archive() -> bool:
    with transaction.atomic():
        job = (
            PhotoArchiveJob.objects.select_for_update(skip_locked=True)
            .filter(status=PhotoArchiveJob.Status.QUEUED)
            .order_by("updated_at", "id")
            .first()
        )
        if job is None:
            return False
        claimed_at = timezone.now()
        claimed_ns = monotonic_ns()
        if job.queue_first_claimed_at is None:
            job.queue_first_claimed_at = claimed_at
        job.status = PhotoArchiveJob.Status.RUNNING
        job.object_id = uuid4()
        job.save(update_fields=["status", "object_id", "updated_at", "queue_first_claimed_at"])
        schedule_transition(ARCHIVE, str(PhotoArchiveJob.Status.QUEUED), job.status)

    try:
        with _reserve_archive_space(job.entries):
            started_ns = monotonic_ns()
            built_ns = _build_and_upload(job)
    except (ArchiveSourceMissing, ArchiveSourceUnavailable, StorageUnavailable, OSError) as error:
        logger.warning(
            "archive preparation failed",
            extra={
                "archive_context": job.context,
                "archive_page": job.page,
                "archive_failure": type(error).__name__,
            },
        )
        _finish(job)
    else:
        capacity_ms = round((started_ns - claimed_ns) / 1_000_000)
        built_ms = round((built_ns - claimed_ns) / 1_000_000)
        finished_ms = round((monotonic_ns() - claimed_ns) / 1_000_000)
        submitted, first = job.queue_enqueued_at, job.queue_first_claimed_at
        segments = None
        if submitted is not None and first is not None and submitted <= first <= claimed_at:
            wait = elapsed_ms(submitted, first)
            until_claim = elapsed_ms(submitted, claimed_at)
            if 0 <= capacity_ms <= built_ms <= finished_ms:
                segments = {
                    "wait": wait,
                    "retry": until_claim - wait,
                    "capacity": capacity_ms,
                    "build": built_ms - capacity_ms,
                    "upload": finished_ms - built_ms,
                }
        _finish(job, ready=True, segments_ms=segments)
    return True


def _expired(job: PhotoArchiveJob) -> bool:
    return job.expires_at is not None and job.expires_at <= timezone.now()


def _build_and_upload(job: PhotoArchiveJob) -> int:
    from commerce.original_delivery import record_archive_original_missing

    entries = []
    for entry in job.entries:
        source_entry = entry.copy()
        order_item_id = source_entry.pop("order_item_id")
        entries.append(
            ArchiveEntry(
                **source_entry,
                on_source_missing=(
                    partial(record_archive_original_missing, order_item_id)
                    if order_item_id is not None
                    else None
                ),
            )
        )
    storage = _archive_storage()
    with TemporaryFile() as body:
        with closing(
            prepare_zip_archive(
                entries=entries,
                storage_factory=lambda: storage,
                observation=ArchiveObservation(context=job.context, page=job.page),
            )
        ) as archive:
            for chunk in archive:
                body.write(chunk)
        body.seek(0)
        built_ns = monotonic_ns()
        storage.upload_archive(object_id=job.object_id, body=body, filename=job.filename)
        return built_ns


def _archive_storage() -> PrivateUploadStorage:
    try:
        return PrivateUploadStorage()
    except (BotoCoreError, ClientError, ValueError):
        raise StorageUnavailable() from None


@transaction.atomic
def _finish(
    job: PhotoArchiveJob, *, ready: bool = False, segments_ms: dict[str, int] | None = None
) -> None:
    finished_at = timezone.now()
    updated = PhotoArchiveJob.objects.filter(
        pk=job.pk, status=PhotoArchiveJob.Status.RUNNING, object_id=job.object_id
    ).update(
        status=PhotoArchiveJob.Status.READY if ready else PhotoArchiveJob.Status.FAILED,
        updated_at=finished_at,
        expires_at=finished_at + _RETENTION if ready else None,
    )
    if updated:
        schedule_transition(
            ARCHIVE,
            str(PhotoArchiveJob.Status.RUNNING),
            str(PhotoArchiveJob.Status.READY if ready else PhotoArchiveJob.Status.FAILED),
            segments_ms=segments_ms,
        )
