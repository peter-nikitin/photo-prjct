"""Bounded event history of the requesting uploader's confirmed photo membership."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.paginator import Page, Paginator
from django.db.models import Count, Q
from picflow.models import Event

from ingestion.models import UploadBatch, UploadItem

BATCH_HISTORY_PAGE_SIZE = 20


@dataclass(frozen=True)
class OwnedBatchHistory:
    id: UUID
    status: str
    created_at: datetime
    expected_count: int
    confirmed_count: int
    failed_count: int
    unresolved_count: int
    can_close: bool


def _owned_event_batches(*, uploader: AbstractBaseUser, event: Event):
    return UploadBatch.objects.filter(event=event, uploader=uploader).annotate(
        confirmed_count=Count("items", filter=Q(items__photo__isnull=False)),
        failed_count=Count(
            "items", filter=Q(items__photo__isnull=True, items__status=UploadItem.Status.FAILED)
        ),
    )


def _project_owned_batches(batches: Iterable[UploadBatch]) -> list[OwnedBatchHistory]:
    summaries = []
    for batch in batches:
        summaries.append(
            OwnedBatchHistory(
                id=batch.pk,
                status=batch.status,
                created_at=batch.created_at,
                expected_count=batch.expected_item_count,
                confirmed_count=batch.confirmed_count,
                failed_count=batch.failed_count,
                unresolved_count=batch.expected_item_count - batch.confirmed_count,
                can_close=batch.confirmed_count == batch.expected_item_count,
            )
        )
    return summaries


def owned_event_batch_summaries(
    *, uploader: AbstractBaseUser, event: Event, batch_ids: Iterable[UUID]
) -> list[OwnedBatchHistory]:
    """Summarize at most one displayed page after owner and event authorization."""
    requested_ids = tuple(dict.fromkeys(batch_ids))
    if len(requested_ids) > BATCH_HISTORY_PAGE_SIZE:
        raise ValueError(
            f"batch summary request must contain at most {BATCH_HISTORY_PAGE_SIZE} IDs"
        )
    summaries = _project_owned_batches(
        _owned_event_batches(uploader=uploader, event=event).filter(pk__in=requested_ids)
    )
    by_id = {row.id: row for row in summaries}
    return [by_id[batch_id] for batch_id in requested_ids if batch_id in by_id]


def owned_event_batch_history(
    *, uploader: AbstractBaseUser, event: Event, page: str | int = 1
) -> Page[OwnedBatchHistory]:
    batches = _owned_event_batches(uploader=uploader, event=event).order_by("-created_at", "-id")
    batch_page = Paginator(batches, BATCH_HISTORY_PAGE_SIZE).get_page(page)
    summaries = _project_owned_batches(batch_page)
    return Page(summaries, batch_page.number, batch_page.paginator)
