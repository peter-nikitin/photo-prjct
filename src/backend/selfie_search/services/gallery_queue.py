"""Durable gallery work stored directly on SelfieSearch; one local executor."""

from datetime import timedelta

from django.db import DatabaseError, transaction
from django.utils import timezone

from selfie_search.models import SelfieSearch
from selfie_search.services.submission import (
    GallerySearchFailed,
    fail_gallery_attempt,
    process_gallery_photo_search,
)

MAX_ATTEMPTS = 3
RETRY_SECONDS = 5


def recover_processing() -> int:
    now = timezone.now()
    return SelfieSearch.objects.filter(
        configuration__processor="gallery_photo_query",
        status="processing",
    ).update(
        status="queued",
        gallery_attempt_count=0,
        gallery_available_at=now,
        state_changed_at=now,
        failure_code="",
        terminal_at=None,
        cleanup_confirmed_at=None,
    )


def claim() -> SelfieSearch | None:
    now = timezone.now()
    with transaction.atomic():
        gallery = SelfieSearch.objects.filter(configuration__processor="gallery_photo_query")
        search = (
            gallery.filter(status="queued", gallery_available_at__lte=now)
            .order_by("created_at", "pk")
            .first()
        )
        if search is None:
            return None
        search.status = "processing"
        search.gallery_attempt_count += 1
        search.state_changed_at = now
        search.save()
        return search


def retry(search: SelfieSearch) -> None:
    now = timezone.now()
    exhausted = search.gallery_attempt_count >= MAX_ATTEMPTS
    SelfieSearch.objects.filter(
        pk=search.pk,
        status="processing",
    ).update(
        status="failed" if exhausted else "queued",
        failure_code="attempts_exhausted" if exhausted else "",
        gallery_available_at=now + timedelta(seconds=RETRY_SECONDS),
        state_changed_at=now,
        terminal_at=now if exhausted else None,
        cleanup_confirmed_at=now if exhausted else None,
    )


def run_once() -> bool:
    search = claim()
    if search is None:
        return False
    try:
        process_gallery_photo_search(search=search)
    except (GallerySearchFailed, DatabaseError):
        retry(search)
    except Exception:
        # Unexpected per-task failure must not strand the rest of the queue.
        fail_gallery_attempt(
            search_id=search.pk,
            status="failed",
            now=timezone.now(),
        )
    return True
