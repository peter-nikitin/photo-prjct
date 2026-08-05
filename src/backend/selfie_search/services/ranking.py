"""Deterministic exact ranking over a search's frozen event-scoped cohort."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from selfie_search.models import SelfieSearch

_NORMALIZATION_TOLERANCE = 1e-6
_SELFIE_QUERY_MODEL = "sface"
_SELFIE_QUERY_DIMENSIONS = 128
_SELFIE_QUERY_THRESHOLD = 0.363


class RankingError(ValueError):
    """The frozen corpus is incompatible with the declared query contract."""


class QueryVectorError(RankingError):
    """A worker callback did not contain one finite normalized query vector."""


@dataclass(frozen=True)
class RankedPhoto:
    photo_id: str
    detection_id: UUID
    cosine_distance: float


@dataclass(frozen=True)
class CandidateEmbedding:
    vector: object
    model_version: str
    detection_id: UUID
    photo_id: str
    photo_event_id: object
    attempt_event_id: object
    attempt_photo_id: object


def rank_embeddings(
    search: SelfieSearch,
    query_vector: object,
    candidates: Iterable[CandidateEmbedding],
) -> tuple[RankedPhoto, ...]:
    """Rank an in-memory compatible cohort without persisting intermediate candidate rows."""
    configuration = _configuration(search)
    query = _normalized_vector(
        query_vector,
        dimensions=configuration.dimensions,
        error_type=QueryVectorError,
    )
    best_by_photo: dict[str, RankedPhoto] = {}
    for candidate in candidates:
        if (
            candidate.photo_event_id != search.event_id
            or candidate.attempt_event_id != search.event_id
            or str(candidate.attempt_photo_id) != candidate.photo_id
        ):
            raise RankingError("candidate identity is outside the frozen search event")
        if candidate.model_version != configuration.model:
            raise RankingError("candidate embedding model is incompatible")
        gallery = _normalized_vector(
            candidate.vector,
            dimensions=configuration.dimensions,
            error_type=RankingError,
        )
        distance = 1.0 - math.fsum(left * right for left, right in zip(query, gallery, strict=True))
        distance = min(2.0, max(0.0, distance))
        if distance > configuration.threshold:
            continue
        ranked = RankedPhoto(
            photo_id=candidate.photo_id,
            detection_id=candidate.detection_id,
            cosine_distance=distance,
        )
        previous = best_by_photo.get(ranked.photo_id)
        if previous is None or ranked.cosine_distance < previous.cosine_distance:
            best_by_photo[ranked.photo_id] = ranked
    return tuple(
        sorted(best_by_photo.values(), key=lambda row: (row.cosine_distance, row.photo_id))
    )


@dataclass(frozen=True)
class _SearchConfiguration:
    model: str
    dimensions: int
    threshold: float


def _configuration(search: SelfieSearch) -> _SearchConfiguration:
    configuration = search.configuration
    if not isinstance(configuration, dict):
        raise RankingError("search configuration is invalid")
    model = configuration.get("embedding_model")
    dimensions = configuration.get("embedding_dimensions")
    threshold = configuration.get("cosine_distance_threshold")
    if (
        model != _SELFIE_QUERY_MODEL
        or isinstance(dimensions, bool)
        or not isinstance(dimensions, int)
        or dimensions != _SELFIE_QUERY_DIMENSIONS
        or isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not math.isclose(
            threshold, _SELFIE_QUERY_THRESHOLD, rel_tol=0.0, abs_tol=_NORMALIZATION_TOLERANCE
        )
    ):
        raise RankingError("search configuration is invalid")
    return _SearchConfiguration(model=model, dimensions=dimensions, threshold=float(threshold))


def validate_query_vector(search: SelfieSearch, query_vector: object) -> tuple[float, ...]:
    """Validate a callback vector without persisting or logging it."""
    return _normalized_vector(
        query_vector,
        dimensions=_configuration(search).dimensions,
        error_type=QueryVectorError,
    )


def _normalized_vector(
    value: object, *, dimensions: int, error_type: type[RankingError]
) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != dimensions:
        raise error_type("embedding dimensions are incompatible")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise error_type("embedding contains a non-finite value")
        vector.append(float(item))
    norm = math.sqrt(math.fsum(item * item for item in vector))
    if not math.isfinite(norm) or not math.isclose(
        norm, 1.0, rel_tol=0.0, abs_tol=_NORMALIZATION_TOLERANCE
    ):
        raise error_type("embedding is not normalized")
    return tuple(vector)
