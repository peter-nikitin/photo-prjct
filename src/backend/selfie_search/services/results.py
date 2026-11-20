"""Public bearer-result lookup and read-time presentation eligibility."""

from __future__ import annotations

import hashlib
from typing import Final

from django.core.paginator import Page, Paginator
from django.db.models import F, Q, QuerySet, Window
from django.db.models.functions import RowNumber
from django.views.decorators.debug import sensitive_variables
from picflow.gallery import saved_result_photo_queryset
from picflow.models import Event, Photo

from selfie_search.models import SelfieSearch, SelfieSearchResult


class PublicSearchNotFound(LookupError):
    """A bearer token does not identify a currently public event result."""


SELFIE_SEARCH_RESULT_PAGE_SIZE: Final = 100


@sensitive_variables("public_token")
def resolve_public_result(*, event_slug: str, public_token: str, user) -> SelfieSearch:
    """Resolve one stable bearer result only while its event is site-visible to the caller."""
    try:
        public_token_digest = hashlib.sha256(public_token.encode("ascii")).hexdigest()
    except UnicodeEncodeError:
        raise PublicSearchNotFound from None
    try:
        return SelfieSearch.objects.select_related("event").get(
            event__in=Event.objects.site_visible_to(user).filter(slug=event_slug),
            public_token_digest=public_token_digest,
            parent_search__isnull=True,
        )
    except SelfieSearch.DoesNotExist:
        raise PublicSearchNotFound from None


def saved_ready_result_photos(
    search: SelfieSearch,
    *,
    paid_watermarked_previews_enabled: bool = False,
) -> tuple[Photo, ...]:
    """Return eligible saved members in pass order, then capture-time order."""
    if search.status != SelfieSearch.Status.READY:
        return ()
    rows = _eligible_saved_result_rows(
        search,
        paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
    )
    return tuple(row.photo for row in rows)


def saved_ready_result_page(
    *,
    search: SelfieSearch,
    page_number: str | None,
    paid_watermarked_previews_enabled: bool = False,
    excluded_photo_ids: frozenset[str] = frozenset(),
) -> Page:
    """Return one page of eligible members in pass and capture-time order."""
    rows = (
        _eligible_saved_result_rows(
            search,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        ).exclude(photo_id__in=excluded_photo_ids)
        if search.status == SelfieSearch.Status.READY
        else ()
    )
    return Paginator(rows, SELFIE_SEARCH_RESULT_PAGE_SIZE).page(page_number or 1)


def saved_ready_result_photo(
    *,
    search: SelfieSearch,
    photo_id: str,
    paid_watermarked_previews_enabled: bool = False,
) -> Photo | None:
    """Return one currently eligible saved bearer member, never a general event photo."""
    row = saved_ready_result_row(
        search=search,
        photo_id=photo_id,
        paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
    )
    return row.photo if row is not None else None


def saved_ready_result_row(
    *,
    search: SelfieSearch,
    photo_id: str,
    paid_watermarked_previews_enabled: bool,
) -> SelfieSearchResult | None:
    if search.status != SelfieSearch.Status.READY:
        return None
    return (
        _eligible_saved_result_rows(
            search,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        )
        .filter(photo_id=photo_id)
        .first()
    )


def _eligible_saved_result_rows(
    search: SelfieSearch,
    *,
    paid_watermarked_previews_enabled: bool,
) -> QuerySet[SelfieSearchResult]:
    return (
        eligible_saved_result_rows(
            search, paid_watermarked_previews_enabled=paid_watermarked_previews_enabled
        )
        .alias(
            first_pass=Window(
                expression=RowNumber(),
                partition_by=[F("photo_id")],
                order_by=[F("search__created_at").asc(), F("search_id").asc()],
            )
        )
        .filter(first_pass=1)
        .order_by(
            "search__created_at",
            "search_id",
            F("photo__capture_time").asc(nulls_last=True),
            "photo_id",
        )
    )


def eligible_saved_result_rows(
    search: SelfieSearch,
    *,
    paid_watermarked_previews_enabled: bool = False,
) -> QuerySet[SelfieSearchResult]:
    """Return visible rows in the root and committed passes, including provenance duplicates."""
    ready_searches = SelfieSearch.objects.filter(
        Q(pk=search.pk) | Q(parent_search=search), status=SelfieSearch.Status.READY
    ).values("pk")
    return (
        SelfieSearchResult.objects.filter(
            search_id__in=ready_searches,
            excluded_by_apparel=False,
            photo__event_id=search.event_id,
            photo__src="",
            photo__original_key__isnull=False,
            photo__original_key__gt="",
            photo__original_size__isnull=False,
            photo__in=saved_result_photo_queryset(
                event=search.event,
                paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
            ),
        )
        .select_related("photo__event", "photo__gallery_media_projection")
        .order_by(F("photo__capture_time").asc(nulls_last=True), "photo_id")
    )


def public_status_payload(search: SelfieSearch) -> dict[str, int | str]:
    """Serialize only the bounded state data that a later polling client needs."""
    payload: dict[str, int | str] = {"status": search.status}
    if search.status == SelfieSearch.Status.READY:
        payload["eligible_photo_count"] = search.eligible_photo_count
        payload["matched_photo_count"] = search.published_matched_photo_count
    return payload
