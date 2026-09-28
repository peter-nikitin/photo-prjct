"""Common scalar result contract for legacy and native direct readers."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from processing.services.face_cohort import _validate_generation

from selfie_search.models import SelfieSearch
from selfie_search.services.cohort_cache import CohortCache, cohort_cache
from selfie_search.services.ranking import (
    RankedPhoto,
    RankingError,
    _configuration,
    rank_cached_embeddings,
    validate_query_vector,
)


@dataclass(frozen=True, slots=True, order=True)
class DirectFaceIdentity:
    detection_id: UUID
    photo_id: str
    attempt_id: UUID
    contract_version: int
    processor_version: int
    configuration_hash: str
    model_version: str


@dataclass(frozen=True, slots=True)
class DirectRankingOutcome:
    photos: tuple[RankedPhoto, ...]
    eligible_face_count: int
    eligible_photo_count: int
    snapshot: tuple[DirectFaceIdentity, ...]
    identity_ms: float
    build_ms: float
    ranking_ms: float
    cache_hit: bool = False
    shortlist_count: int = 0
    # Opt-in best rows before threshold filtering explain boundary membership changes.
    best_candidates: tuple[RankedPhoto, ...] = ()


def frozen_generations(search: SelfieSearch) -> Sequence[Mapping[str, object]]:
    configuration = _configuration(search)
    generations = search.configuration.get("gallery_face_embedding_generations")
    if not isinstance(generations, list) or not generations:
        raise RankingError("invalid face-embedding generation")
    for generation in generations:
        try:
            _validate_generation(generation)
        except (ValueError, TypeError) as error:
            raise RankingError("invalid face-embedding generation") from error
        if generation["model"] != configuration.model:
            raise RankingError("frozen face-embedding model is incompatible")
    return generations


def rank_legacy_direct(
    search: SelfieSearch,
    query_vector: object,
    *,
    comparison_evidence: bool = False,
    cache: CohortCache | None = None,
) -> DirectRankingOutcome:
    """Adapt the existing cache and arithmetic without changing its reader."""
    configuration = _configuration(search)
    generations = frozen_generations(search)
    lookup = (cache if cache is not None else cohort_cache).get(
        event=search.event,
        generations=generations,
        model=configuration.model,
        dimensions=configuration.dimensions,
    )
    started = perf_counter()
    ranking = rank_cached_embeddings(search, query_vector, lookup.entry)
    best_candidates: dict[str, RankedPhoto] = {}
    if comparison_evidence:
        query = validate_query_vector(search, query_vector)
        for face, vector in zip(lookup.entry.faces, lookup.entry.matrix, strict=True):
            distance = min(
                2.0,
                max(
                    0.0,
                    1.0
                    - math.fsum(
                        left * right for left, right in zip(query, vector.tolist(), strict=True)
                    ),
                ),
            )
            row = RankedPhoto(face.photo_id, face.detection_id, distance)
            previous = best_candidates.get(face.photo_id)
            if previous is None or (distance, face.detection_id) < (
                previous.cosine_distance,
                previous.detection_id,
            ):
                best_candidates[face.photo_id] = row
    ranking_ms = (perf_counter() - started) * 1_000
    snapshot = tuple(
        sorted(
            DirectFaceIdentity(
                row.detection_id,
                row.photo_id,
                row.attempt_id,
                row.contract_version,
                row.processor_version,
                row.configuration_hash,
                row.model_version,
            )
            for row in lookup.entry.fingerprint
        )
    )
    return DirectRankingOutcome(
        photos=ranking.photos,
        eligible_face_count=len(lookup.entry.faces),
        eligible_photo_count=len({row.photo_id for row in lookup.entry.faces}),
        snapshot=snapshot,
        identity_ms=lookup.identity_ms,
        build_ms=lookup.build_ms,
        ranking_ms=ranking_ms,
        cache_hit=lookup.cache_hit,
        shortlist_count=ranking.shortlist_count,
        best_candidates=tuple(
            sorted(best_candidates.values(), key=lambda row: (row.cosine_distance, row.photo_id))
        ),
    )
