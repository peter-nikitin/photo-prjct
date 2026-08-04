from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Count, Exists, OuterRef, Prefetch, Q

from ingestion.models import UploadBatch, UploadItem

_RESUMABLE_STATUSES = (
    UploadBatch.Status.CREATED,
    UploadBatch.Status.UPLOADING,
    UploadBatch.Status.PARTIAL,
    UploadBatch.Status.FAILED,
)


@dataclass(frozen=True)
class UnfinishedBatchSummary:
    id: UUID
    event_id: int
    event_name: str
    status: str
    created_at: datetime
    last_activity_at: datetime
    expected_count: int
    confirmed_count: int
    failed_count: int
    unresolved_count: int


@dataclass(frozen=True)
class ResumeManifestItem:
    id: UUID
    filename: str
    size: int
    last_modified_ms: int | None
    ambiguous_sha256: str | None
    status: str
    confirmed: bool


@dataclass(frozen=True)
class ResumeManifest:
    id: UUID
    event_id: int
    event_name: str
    expected_count: int
    items: tuple[ResumeManifestItem, ...]


def list_unfinished_batches(uploader: AbstractBaseUser) -> tuple[UnfinishedBatchSummary, ...]:
    """Return only resumable batches belonging to the requesting uploader."""
    rows = _owned_unfinished_batches(uploader).annotate(
        confirmed_count=Count("items", filter=Q(items__photo__isnull=False)),
        failed_count=Count(
            "items",
            filter=Q(items__status=UploadItem.Status.FAILED, items__photo__isnull=True),
        ),
    )
    return tuple(
        UnfinishedBatchSummary(
            id=row.id,
            event_id=row.event_id,
            event_name=row.event.name,
            status=row.status,
            created_at=row.created_at,
            last_activity_at=row.last_activity_at,
            expected_count=row.expected_item_count,
            confirmed_count=row.confirmed_count,
            failed_count=row.failed_count,
            unresolved_count=row.expected_item_count - row.confirmed_count,
        )
        for row in rows
    )


def get_resume_manifest(uploader: AbstractBaseUser, batch_id: UUID) -> ResumeManifest:
    """Return one owned resumable batch with only browser-safe matching state."""
    item_fields = (
        "id",
        "batch_id",
        "original_filename",
        "expected_size",
        "client_last_modified_ms",
        "ambiguous_sha256",
        "status",
        "photo_id",
    )
    batch = (
        _owned_unfinished_batches(uploader)
        .filter(pk=batch_id)
        .prefetch_related(
            Prefetch(
                "items",
                queryset=(
                    UploadItem.objects.filter(batch__uploader_id=uploader.pk)
                    .only(*item_fields)
                    .order_by("id")
                ),
            )
        )
        .get()
    )
    return ResumeManifest(
        id=batch.id,
        event_id=batch.event_id,
        event_name=batch.event.name,
        expected_count=batch.expected_item_count,
        items=tuple(
            ResumeManifestItem(
                id=item.id,
                filename=item.original_filename,
                size=item.expected_size,
                last_modified_ms=item.client_last_modified_ms,
                ambiguous_sha256=item.ambiguous_sha256,
                status=item.status,
                confirmed=item.photo_id is not None,
            )
            for item in batch.items.all()
        ),
    )


def _owned_unfinished_batches(uploader: AbstractBaseUser):
    unconfirmed_items = UploadItem.objects.filter(
        batch_id=OuterRef("pk"),
        batch__uploader_id=uploader.pk,
        photo__isnull=True,
    )
    return (
        UploadBatch.objects.filter(
            uploader_id=uploader.pk,
            status__in=_RESUMABLE_STATUSES,
        )
        .filter(Exists(unconfirmed_items))
        .select_related("event")
        .order_by("-last_activity_at", "-created_at")
    )
