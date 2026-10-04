"""Scalar result contract and frozen-generation validation for native ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from processing.services.face_cohort import _validate_generation

from selfie_search.models import SelfieSearch
from selfie_search.services.ranking import (
    RankedPhoto,
    RankingError,
    _configuration,
)


@dataclass(frozen=True, slots=True)
class DirectRankingOutcome:
    photos: tuple[RankedPhoto, ...]
    eligible_face_count: int
    eligible_photo_count: int
    identity_ms: float
    build_ms: float
    ranking_ms: float
    shortlist_count: int = 0


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
