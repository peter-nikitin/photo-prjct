"""Private bounded review using gallery faces, never creating a public result."""

import json
from time import perf_counter
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from picflow.models import Event

from selfie_search.models import SelfieSearch
from selfie_search.services.cohort_cache import CohortCache
from selfie_search.services.direct_ranking import rank_legacy_direct
from selfie_search.services.ranking import _configuration
from selfie_search.services.reader_comparison import (
    classify_expansions,
    compare_outcomes,
    verify_source_representations,
)
from selfie_search.services.submission import (
    _compatible_gallery_embeddings,
    _expand_gallery_ranking,
    _gallery_configuration,
    _gallery_source_candidate,
)
from selfie_search.services.vector_ranking import rank_vector_direct


class Command(BaseCommand):
    help = "Bounded private gallery comparison. No bearer result or biometric report is created."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--event-slug", required=True)
        parser.add_argument("--max-sources", type=int, default=100)
        parser.add_argument("--repeats", type=int, default=10)

    def handle(self, *args: Any, **options: Any) -> None:
        maximum, repeats = options["max_sources"], options["repeats"]
        if not 1 <= maximum <= 100 or not 1 <= repeats <= 30:
            raise CommandError("max-sources must be 1..100 and repeats 1..30")
        try:
            event = Event.objects.get(slug=options["event_slug"])
        except Event.DoesNotExist:
            raise CommandError("Event unavailable") from None
        configuration = _gallery_configuration(event=event)
        sources = list(
            _compatible_gallery_embeddings(event=event, configuration=configuration)
            .order_by("detection_id")
            .values_list("detection_id", "detection__attempt__photo_id")[:maximum]
        )
        totals: dict[str, Any] = {
            "source_count": len(sources),
            "repeats": repeats,
            "dimensions": configuration["embedding_dimensions"],
            "unexplained_count": 0,
            "boundary_membership_count": 0,
            "boundary_order_count": 0,
            "boundary_detection_count": 0,
            "insufficient_detection_evidence_count": 0,
            "legacy_cache_hits": 0,
            "boundary_anchor_count": 0,
            "expansion_membership_difference_count": 0,
            "expansion_order_changed_count": 0,
            "max_distance_delta": 0.0,
            "failed_count": 0,
            "legacy_total_ms": [],
            "legacy_cold_total_ms": [],
            "legacy_warm_total_ms": [],
            "native_total_ms": [],
            "legacy_diagnostic_ms": [],
            "native_diagnostic_ms": [],
            "incomplete_expansion_count": 0,
            "boundary_expansion_effect_count": 0,
            "cache_states": "first cold application cache, subsequent warm; DB cache uncontrolled",
        }
        review_cache = CohortCache()
        if not sources:
            raise CommandError("No eligible gallery sources; no comparison evidence")
        for repeat in range(repeats):
            for detection, photo in sources:
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                            )
                        frozen = dict(
                            configuration,
                            query_source={
                                "kind": "gallery_photo",
                                "photo_id": str(photo),
                                "detection_id": str(detection),
                            },
                        )
                        search = SelfieSearch(event=event, configuration=frozen)
                        legacy_source = _gallery_source_candidate(
                            event=event, configuration=frozen, reader="legacy"
                        )
                        native_source = _gallery_source_candidate(
                            event=event, configuration=frozen, reader="pgvector"
                        )
                        verify_source_representations(legacy_source, native_source)
                        outcomes = {}
                        for reader in (
                            ("legacy", "native") if repeat % 2 == 0 else ("native", "legacy")
                        ):
                            start = perf_counter()
                            if reader == "legacy":
                                outcomes[reader] = rank_legacy_direct(
                                    search,
                                    legacy_source.vector,
                                    cache=review_cache,
                                )
                            else:
                                outcomes[reader] = rank_vector_direct(search, legacy_source.vector)
                            elapsed = (perf_counter() - start) * 1000
                            totals[f"{reader}_total_ms"].append(elapsed)
                            if reader == "legacy":
                                cache_state = "warm" if outcomes[reader].cache_hit else "cold"
                                totals[f"legacy_{cache_state}_total_ms"].append(elapsed)
                        diagnostics = {}
                        for reader in ("legacy", "native"):
                            start = perf_counter()
                            if reader == "legacy":
                                diagnostics[reader] = rank_legacy_direct(
                                    search,
                                    legacy_source.vector,
                                    comparison_evidence=True,
                                    cache=review_cache,
                                )
                            else:
                                diagnostics[reader] = rank_vector_direct(
                                    search, legacy_source.vector, comparison_evidence=True
                                )
                            totals[f"{reader}_diagnostic_ms"].append(
                                (perf_counter() - start) * 1000
                            )
                        totals["legacy_cache_hits"] += int(outcomes["legacy"].cache_hit)
                        from processing.models import EventFaceClusterActivation

                        activation = EventFaceClusterActivation.objects.filter(
                            event=event, active=True
                        ).first()
                        expansions = {
                            key: _expand_gallery_ranking(
                                search=search, ranked=value.photos, query=legacy_source.vector
                            )
                            for key, value in outcomes.items()
                        }
                        if any(
                            not any(row.photo_id == str(photo) for row in value.photos)
                            for value in outcomes.values()
                        ):
                            totals["unexplained_count"] += 1
                        report = compare_outcomes(
                            diagnostics["legacy"],
                            diagnostics["native"],
                            threshold=_configuration(search).threshold,
                            anchor_threshold=activation.anchor_threshold if activation else None,
                        )
                        classify_expansions(
                            report,
                            expansions["legacy"],
                            expansions["native"],
                            expansion_expected=activation is not None,
                        )
                        totals["expansion_order_changed_count"] += int(
                            report["expansion_order_changed"]
                        )
                        for key in (
                            "unexplained_count",
                            "boundary_membership_count",
                            "boundary_order_count",
                            "boundary_detection_count",
                            "insufficient_detection_evidence_count",
                            "boundary_anchor_count",
                            "expansion_membership_difference_count",
                            "incomplete_expansion_count",
                            "boundary_expansion_effect_count",
                        ):
                            totals[key] += report[key]
                        totals["max_distance_delta"] = max(
                            totals["max_distance_delta"], report["max_distance_delta"]
                        )
                except Exception:
                    totals["failed_count"] += 1
        self.stdout.write(json.dumps(totals, sort_keys=True))
        if totals["unexplained_count"] or totals["failed_count"]:
            raise CommandError("Review failed; unexplained differences or unavailable comparisons")
