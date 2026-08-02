"""Public bearer-result lookup and read-time presentation eligibility."""

from __future__ import annotations

import hashlib
from typing import Final

from django.core.paginator import Page, Paginator
from picflow.gallery import gallery_photo_queryset
from picflow.models import Event, Photo

from selfie_search.models import SelfieSearch, SelfieSearchResult


class PublicSearchNotFound(LookupError):
    """A bearer token does not identify a currently public event result."""


SELFIE_SEARCH_RESULT_PAGE_SIZE: Final = 100


def resolve_public_result(*, event_slug: str, public_token: str) -> SelfieSearch:
    """Resolve one stable bearer result only while its event remains published."""
    try:
        public_token_digest = hashlib.sha256(public_token.encode("ascii")).hexdigest()
    except UnicodeEncodeError:
        raise PublicSearchNotFound from None
    try:
        return SelfieSearch.objects.select_related("event").get(
            event__slug=event_slug,
            event__publication_status=Event.PublicationStatus.PUBLISHED,
            public_token_digest=public_token_digest,
        )
    except SelfieSearch.DoesNotExist:
        raise PublicSearchNotFound from None


def saved_ready_result_photos(search: SelfieSearch) -> tuple[Photo, ...]:
    """Return current eligible saved members in their immutable persisted rank order."""
    if search.status != SelfieSearch.Status.READY:
        return ()
    rows = _eligible_saved_result_rows(search)
    return tuple(row.photo for row in rows)


def saved_ready_result_page(*, search: SelfieSearch, page_number: str | None) -> Page:
    """Return one bounded current presentation page of the immutable saved result."""
    rows = _eligible_saved_result_rows(search) if search.status == SelfieSearch.Status.READY else ()
    return Paginator(rows, SELFIE_SEARCH_RESULT_PAGE_SIZE).page(page_number or 1)


def saved_ready_result_photo(*, search: SelfieSearch, photo_id: str) -> Photo | None:
    """Return one currently eligible saved bearer member, never a general event photo."""
    if search.status != SelfieSearch.Status.READY:
        return None
    row = _eligible_saved_result_rows(search).filter(photo_id=photo_id).first()
    return row.photo if row is not None else None


def _eligible_saved_result_rows(search: SelfieSearch):
    return (
        SelfieSearchResult.objects.filter(
            search=search,
            photo__event_id=search.event_id,
            photo__src="",
            photo__original_key__isnull=False,
            photo__original_key__gt="",
            photo__original_size__isnull=False,
            photo__in=gallery_photo_queryset(event=search.event),
        )
        .select_related("photo__event")
        .order_by("rank", "photo_id")
    )


def public_status_payload(search: SelfieSearch) -> dict[str, int | str]:
    """Serialize only the bounded state data that a later polling client needs."""
    payload: dict[str, int | str] = {"status": search.status}
    if search.status == SelfieSearch.Status.READY:
        payload["eligible_photo_count"] = search.eligible_photo_count
        payload["matched_photo_count"] = search.matched_photo_count
    return payload
