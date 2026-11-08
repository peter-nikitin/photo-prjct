"""Scalar result contract for native ranking."""

from __future__ import annotations

from dataclasses import dataclass

from selfie_search.services.ranking import RankedPhoto


@dataclass(frozen=True, slots=True)
class DirectRankingOutcome:
    photos: tuple[RankedPhoto, ...]
    eligible_face_count: int
    eligible_photo_count: int
    has_vectors: bool
    identity_ms: float
    build_ms: float
    ranking_ms: float
    shortlist_count: int = 0
