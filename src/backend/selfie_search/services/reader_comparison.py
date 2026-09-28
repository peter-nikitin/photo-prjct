"""Explicit private dual-reader review; only aggregate evidence may leave this module."""

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

import numpy as np
from django.db import connection, transaction

from selfie_search.models import SelfieSearch
from selfie_search.services.cluster_expansion import RankedPhotoExpansion
from selfie_search.services.direct_ranking import DirectRankingOutcome, rank_legacy_direct
from selfie_search.services.ranking import CandidateEmbedding, RankedPhoto
from selfie_search.services.read_selection import Reader
from selfie_search.services.vector_ranking import rank_vector_direct

logger = logging.getLogger(__name__)
TOLERANCE = 1e-6


def emit_comparison(report: dict[str, Any]) -> None:
    # Deliberately no event/search/detection identifiers or exception text.
    try:
        logger.info("selfie_reader_comparison %s", json.dumps(report, sort_keys=True))
    except Exception:
        pass


@contextmanager
def comparison_snapshot(search: SelfieSearch) -> Iterator[bool]:
    requested = search.reader_comparison_requested and search.reader_staff_eligible
    if not requested:
        yield False
        return
    if connection.in_atomic_block:
        emit_comparison({"outcome": "snapshot_unavailable"})
        yield False
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield True


def verify_source_representations(selected: CandidateEmbedding, other: CandidateEmbedding) -> None:
    fields = (
        "model_version",
        "detection_id",
        "photo_id",
        "photo_event_id",
        "attempt_event_id",
        "attempt_photo_id",
    )
    if any(getattr(selected, name) != getattr(other, name) for name in fields):
        raise ValueError("source identity mismatch")
    if not np.array_equal(
        np.asarray(selected.vector, dtype=np.float32), np.asarray(other.vector, dtype=np.float32)
    ):
        raise ValueError("source value mismatch")


def compare_outcomes(
    legacy: DirectRankingOutcome,
    native: DirectRankingOutcome,
    *,
    threshold: float,
    anchor_threshold: float | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = dict(
        unexplained_count=0,
        boundary_membership_count=0,
        boundary_detection_count=0,
        insufficient_detection_evidence_count=0,
        boundary_order_count=0,
        boundary_anchor_count=0,
        max_distance_delta=0.0,
    )
    if (legacy.snapshot, legacy.eligible_face_count, legacy.eligible_photo_count) != (
        native.snapshot,
        native.eligible_face_count,
        native.eligible_photo_count,
    ):
        report["unexplained_count"] += 1
    left = {row.photo_id: row for row in legacy.best_candidates}
    right = {row.photo_id: row for row in native.best_candidates}
    lm = {row.photo_id for row in legacy.photos}
    rm = {row.photo_id for row in native.photos}
    report["unexplained_count"] += len(left.keys() ^ right.keys())
    for photo in left.keys() & right.keys():
        a, b = left[photo], right[photo]
        delta = abs(a.cosine_distance - b.cosine_distance)
        report["max_distance_delta"] = max(report["max_distance_delta"], delta)
        if delta > TOLERANCE:
            report["unexplained_count"] += 1
        if (photo in lm) != (photo in rm):
            boundary = (
                max(abs(a.cosine_distance - threshold), abs(b.cosine_distance - threshold))
                <= TOLERANCE
            )
            report["boundary_membership_count" if boundary else "unexplained_count"] += 1
        if a.detection_id != b.detection_id:
            report["insufficient_detection_evidence_count"] += 1
            # Best minima alone cannot prove both selected detections are near ties.
            report["unexplained_count"] += 1
        if anchor_threshold is not None and (a.cosine_distance <= anchor_threshold) != (
            b.cosine_distance <= anchor_threshold
        ):
            boundary = (
                max(
                    abs(a.cosine_distance - anchor_threshold),
                    abs(b.cosine_distance - anchor_threshold),
                )
                <= TOLERANCE
            )
            report["boundary_anchor_count" if boundary else "unexplained_count"] += 1
    # Any reordered component must span at most the accepted numerical band.
    common = lm & rm
    legacy_order = [row for row in legacy.photos if row.photo_id in common]
    native_order = [row for row in native.photos if row.photo_id in common]
    component_start = 0
    pending: set[str] = set()
    for index, (a, b) in enumerate(zip(legacy_order, native_order, strict=True)):
        pending.symmetric_difference_update((a.photo_id,))
        pending.symmetric_difference_update((b.photo_id,))
        if not pending:
            if component_start != index:
                legacy_span = (
                    legacy_order[index].cosine_distance
                    - legacy_order[component_start].cosine_distance
                )
                native_span = (
                    native_order[index].cosine_distance
                    - native_order[component_start].cosine_distance
                )
                boundary = max(legacy_span, native_span) <= TOLERANCE
                report["boundary_order_count" if boundary else "unexplained_count"] += 1
            component_start = index + 1
    return report


def classify_expansions(
    report: dict[str, Any],
    left: RankedPhotoExpansion,
    right: RankedPhotoExpansion,
    *,
    expansion_expected: bool = False,
) -> None:
    """Classify optional expansion evidence separately from selected publication."""
    left_ids = tuple(row.photo_id for row in left.results)
    right_ids = tuple(row.photo_id for row in right.results)
    report["expansion_membership_difference_count"] = len(set(left_ids) ^ set(right_ids))
    report["expansion_order_changed"] = left_ids != right_ids
    report["strong_anchor_count_delta"] = abs(left.strong_anchor_count - right.strong_anchor_count)
    report["incomplete_expansion_count"] = 0
    report["boundary_expansion_effect_count"] = 0
    unavailable = {"corpus_unavailable", "corpus_incompatible"}
    incomplete = (left.outcome in unavailable or right.outcome in unavailable) and (
        expansion_expected or left.outcome != right.outcome or left.outcome == "corpus_incompatible"
    )
    if incomplete:
        report["incomplete_expansion_count"] = 1
        report["unexplained_count"] += 1
        return
    left_expanded = tuple(
        row.photo_id for row in left.results if row.primary_source == "face_cluster_expansion"
    )
    right_expanded = tuple(
        row.photo_id for row in right.results if row.primary_source == "face_cluster_expansion"
    )
    effect = (
        left_expanded != right_expanded
        or left.strong_anchor_count != right.strong_anchor_count
        or left.expanded_cluster_count != right.expanded_cluster_count
    )
    if effect:
        if report["boundary_anchor_count"] and not report["unexplained_count"]:
            report["boundary_expansion_effect_count"] = 1
        else:
            report["unexplained_count"] += 1


def review_other_reader(
    *,
    search: SelfieSearch,
    query: object,
    reader: Reader,
    selected: DirectRankingOutcome,
    expansion: RankedPhotoExpansion,
    expand: Callable[[tuple[RankedPhoto, ...]], RankedPhotoExpansion],
    source_verifier: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Savepoint isolates SQL review failures from a successful selected result."""
    started = perf_counter()
    try:
        with transaction.atomic():
            if source_verifier is not None:
                source_verifier()
            ranker = rank_legacy_direct if reader == "pgvector" else rank_vector_direct
            other = ranker(search, query, comparison_evidence=True)
            other_expansion = expand(other.photos)
            from processing.models import EventFaceClusterActivation

            activation = EventFaceClusterActivation.objects.filter(
                event=search.event, active=True
            ).first()
            report = compare_outcomes(
                other if reader == "pgvector" else selected,
                selected if reader == "pgvector" else other,
                threshold=float(search.configuration["cosine_distance_threshold"]),
                anchor_threshold=activation.anchor_threshold if activation else None,
            )
            source = search.configuration.get("query_source")
            if isinstance(source, dict) and not any(
                row.photo_id == source.get("photo_id") for row in other.photos
            ):
                report["unexplained_count"] += 1
            classify_expansions(
                report, expansion, other_expansion, expansion_expected=activation is not None
            )
            report["selected_diagnostic_ms"] = (
                selected.identity_ms + selected.build_ms + selected.ranking_ms
            )
            report["comparison_total_ms"] = (perf_counter() - started) * 1000
            report["outcome"] = "unexplained" if report["unexplained_count"] else "compared"
    except Exception:
        report = {"outcome": "comparison_failed"}
    emit_comparison(report)
    return report
