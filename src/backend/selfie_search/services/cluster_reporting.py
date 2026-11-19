"""Privacy-bounded retrospective aggregates for face-cluster expansion."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import CharField, Count, Q, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from picflow.models import Event
from search_feedback.models import SearchFeedback

from selfie_search.models import SelfieSearch, SelfieSearchResult

MOSCOW = ZoneInfo("Europe/Moscow")
NOT_AVAILABLE = "not_available"

_AVAILABLE_SNAPSHOT = Q(
    direct_matched_photo_count__isnull=False,
    cluster_expanded_photo_count__isnull=False,
    final_matched_photo_count__isnull=False,
)
_RESULT_SOURCES = {
    "direct": SelfieSearchResult.PrimarySource.DIRECT,
    "expanded": SelfieSearchResult.PrimarySource.FACE_CLUSTER_EXPANSION,
}


def build_cluster_expansion_report(
    *,
    start: date | datetime,
    end: date | datetime,
    event: Event | UUID | int | str | None = None,
) -> dict[str, Any]:
    """Build one aggregate-only report for a closed-open Moscow calendar window.

    Search rows with no complete expansion snapshot are historical and intentionally excluded from
    source/label aggregates.  They are represented as ``not_available`` when no current-contract
    rows are present in the requested window.  ``direct`` is the complete direct-primary cohort,
    including its ``dual_evidence`` subset; ``expanded`` is the face-cluster-primary cohort.
    """
    start_bound = _bound(start, name="start")
    end_bound = _bound(end, name="end")
    if start_bound >= end_bound:
        raise ValueError("start must be before end")
    event_id = _event_id(event)

    searches = SelfieSearch.objects.filter(
        created_at__gte=start_bound,
        created_at__lt=end_bound,
    )
    if event_id is not None:
        searches = searches.filter(event_id=event_id)

    total_searches = searches.count()
    available_searches = searches.filter(_AVAILABLE_SNAPSHOT)
    available_count = available_searches.count()
    historical_count = total_searches - available_count
    search_summary = {
        "total": total_searches,
        "available": available_count,
        "historical": historical_count,
    }

    if available_count == 0:
        if historical_count:
            unavailable_results: Any = NOT_AVAILABLE
            unavailable_feedback: Any = NOT_AVAILABLE
        else:
            zero_metrics = _zero_report_metrics()
            unavailable_results = zero_metrics["results"]
            unavailable_feedback = zero_metrics["feedback"]
        return {
            "window": {
                "start": start_bound.date().isoformat(),
                "end": end_bound.date().isoformat(),
                "timezone": "Europe/Moscow",
            },
            "searches": search_summary,
            "policy_hashes": NOT_AVAILABLE if historical_count else [],
            "results": unavailable_results,
            "feedback": unavailable_feedback,
        }

    results = SelfieSearchResult.objects.filter(search__in=available_searches)
    result_volume = _result_volume(results)
    feedback = _feedback_aggregates(results=results)
    return {
        "window": {
            "start": start_bound.date().isoformat(),
            "end": end_bound.date().isoformat(),
            "timezone": "Europe/Moscow",
        },
        "searches": search_summary,
        "policy_hashes": _policy_hashes(available_searches),
        "results": result_volume,
        "feedback": feedback,
    }


def _policy_hashes(searches) -> list[dict[str, str | int]]:  # noqa: ANN001
    return [
        {"hash": row["cluster_configuration_hash"], "searches": row["searches"]}
        for row in searches.exclude(cluster_configuration_hash__isnull=True)
        .values("cluster_configuration_hash")
        .annotate(searches=Count("id"))
        .order_by("cluster_configuration_hash")
    ]


def _bound(value: date | datetime, *, name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")
        return value.astimezone(MOSCOW)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=MOSCOW)
    raise ValueError(f"{name} must be a date")


def _event_id(event: Event | UUID | int | str | None) -> UUID | int | None:
    if event is None:
        return None
    raw = event.pk if isinstance(event, Event) else event
    if isinstance(raw, (UUID, int)) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            raise ValueError("event must be a UUID") from None
    raise ValueError("event must be a UUID")


def _result_volume(results) -> dict[str, int]:  # noqa: ANN001
    direct = results.filter(primary_source=_RESULT_SOURCES["direct"])
    expanded = results.filter(primary_source=_RESULT_SOURCES["expanded"])
    dual = direct.filter(cluster_evidence__isnull=False).distinct()
    return {
        "direct": direct.count(),
        "expanded": expanded.count(),
        "dual_evidence": dual.count(),
    }


def _feedback_aggregates(*, results) -> dict[str, dict[str, Any]]:  # noqa: ANN001
    first_submissions = (
        SearchFeedback.objects.order_by("submission_id", "id")
        .distinct("submission_id")
        .values("id")
    )
    labels = SearchFeedback.objects.filter(pk__in=Subquery(first_submissions)).annotate(
        saved_result_id=KeyTextTransform("result_id", "search_context")
    )
    direct = results.filter(primary_source=_RESULT_SOURCES["direct"])
    expanded = results.filter(primary_source=_RESULT_SOURCES["expanded"])
    dual = direct.filter(cluster_evidence__isnull=False).distinct()
    return {
        name: _source_feedback(
            labels.filter(
                saved_result_id__in=rows.annotate(text_pk=Cast("pk", CharField())).values("text_pk")
            ),
            results_volume=rows.count(),
        )
        for name, rows in (("direct", direct), ("expanded", expanded), ("dual_evidence", dual))
    }


def _source_feedback(labels, *, results_volume: int) -> dict[str, Any]:  # noqa: ANN001
    absent = Q(value=SearchFeedback.Value.ABSENT)
    counts = labels.aggregate(
        present=Count("pk", filter=Q(value=SearchFeedback.Value.PRESENT)),
        absent=Count("pk", filter=absent),
        historical_absent=Count("pk", filter=absent & Q(legacy_attachment_id__isnull=False)),
        labelled=Count("saved_result_id", distinct=True),
    )
    labelled = counts["labelled"]
    return {
        "volume": results_volume,
        "present": counts["present"],
        "absent": counts["absent"],
        "labelled": labelled,
        "unmarked": max(results_volume - labelled, 0),
        "coverage": _ratio(labelled, results_volume),
        "labelled_sample_precision": _ratio(
            counts["present"], counts["present"] + counts["historical_absent"]
        ),
    }


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _zero_report_metrics() -> dict[str, Mapping[str, Any]]:
    feedback = {
        source: _source_feedback(
            SearchFeedback.objects.none().annotate(
                saved_result_id=KeyTextTransform("result_id", "search_context")
            ),
            results_volume=0,
        )
        for source in ("direct", "expanded", "dual_evidence")
    }
    return {
        "results": {"direct": 0, "expanded": 0, "dual_evidence": 0},
        "feedback": feedback,
    }
