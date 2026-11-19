from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from contextlib import closing
from datetime import timedelta
from functools import partial
from hashlib import sha256
from tempfile import TemporaryFile
from time import monotonic_ns
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from config.queue_events import notify_queue_completion
from django.db import transaction
from django.db.models import Q
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


def enqueue_archive_job(
    *,
    scope: str,
    entries: Sequence[ArchiveEntry],
    filename: str,
    observation: ArchiveObservation,
    order_item_ids: Sequence[int] = (),
) -> PhotoArchiveJob:
    """Enqueue or reuse work for a freshly authorized, exact page of originals."""
    if len(entries) > _MAX_PAGE_ENTRIES:
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
        job, _ = PhotoArchiveJob.objects.select_for_update().get_or_create(
            request_key=request_key,
            defaults={
                "entries": payload,
                "filename": filename,
                "context": observation.context,
                "page": observation.page,
            },
        )
        if job.status == PhotoArchiveJob.Status.FAILED or _expired(job):
            job.status = PhotoArchiveJob.Status.QUEUED
            job.public_id = uuid4()
            job.expires_at = None
            job.save(update_fields=["status", "public_id", "expires_at", "updated_at"])
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


def recover_interrupted_archives() -> None:
    """Requeue work abandoned by the previous holder of the worker lock."""
    PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.RUNNING).update(
        status=PhotoArchiveJob.Status.QUEUED,
        updated_at=timezone.now(),
    )


def prune_archive_jobs() -> None:
    now = timezone.now()
    PhotoArchiveJob.objects.filter(
        Q(status=PhotoArchiveJob.Status.READY, expires_at__lte=now)
        | Q(
            status__in=[PhotoArchiveJob.Status.FAILED, PhotoArchiveJob.Status.QUEUED],
            updated_at__lte=now - _RETENTION,
        )
    ).delete()


def process_next_archive() -> bool:
    with transaction.atomic():
        job = (
            PhotoArchiveJob.objects.select_for_update()
            .filter(status=PhotoArchiveJob.Status.QUEUED)
            .order_by("updated_at", "id")
            .first()
        )
        if job is None:
            return False
        wait_ms = max(0, round((timezone.now() - job.updated_at).total_seconds() * 1000))
        job.status = PhotoArchiveJob.Status.RUNNING
        job.object_id = uuid4()
        job.save(update_fields=["status", "object_id", "updated_at"])

    started_ns = monotonic_ns()
    try:
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
        built_ms = round((built_ns - started_ns) / 1_000_000)
        elapsed_ms = round((monotonic_ns() - started_ns) / 1_000_000)
        _finish(
            job,
            segments_ms={
                "archive_wait": wait_ms,
                "zip_build": built_ms,
                "storage_upload": elapsed_ms - built_ms,
            },
        )
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
def _finish(job: PhotoArchiveJob, *, segments_ms: dict[str, int] | None = None) -> None:
    ready = segments_ms is not None
    finished_at = timezone.now()
    updated = PhotoArchiveJob.objects.filter(
        pk=job.pk, status=PhotoArchiveJob.Status.RUNNING, object_id=job.object_id
    ).update(
        status=PhotoArchiveJob.Status.READY if ready else PhotoArchiveJob.Status.FAILED,
        updated_at=finished_at,
        expires_at=finished_at + _RETENTION if ready else None,
    )
    if updated and segments_ms is not None:
        transaction.on_commit(
            partial(notify_queue_completion, "photo_archive", segments_ms),
            robust=True,
        )
