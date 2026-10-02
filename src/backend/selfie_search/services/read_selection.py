"""Current read policy shared by gallery and asynchronous selfie searches."""

import logging
from typing import Literal

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from feature_flags.registry import PGVECTOR_FACE_SEARCH_READ
from feature_flags.services import is_enabled_for_staff_eligibility
from processing.services.vector_embeddings import generation_uses_vector_only_storage

from selfie_search.models import SelfieSearch
from selfie_search.observability import MAX_BOUNDED_INTEGER, SelfieEventName, emit_selfie_event
from selfie_search.services.direct_ranking import DirectRankingOutcome, rank_legacy_direct
from selfie_search.services.ranking import RankingError
from selfie_search.services.vector_ranking import rank_vector_direct

logger = logging.getLogger(__name__)

Reader = Literal["legacy", "pgvector"]


def staff_eligible(user: AbstractBaseUser | AnonymousUser) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def select_reader(search: SelfieSearch) -> Reader:
    if requires_native_reader(search.configuration):
        return "pgvector"
    return (
        "pgvector"
        if is_enabled_for_staff_eligibility(
            PGVECTOR_FACE_SEARCH_READ, staff_eligible=search.reader_staff_eligible
        )
        else "legacy"
    )


def requires_native_reader(configuration: dict) -> bool:
    generations = configuration.get("gallery_face_embedding_generations", [])
    if not isinstance(generations, list):
        raise RankingError("invalid face-embedding generation")
    try:
        return any(
            generation_uses_vector_only_storage(generation)
            for generation in generations
            if isinstance(generation, dict)
        )
    except ValueError as error:
        raise RankingError("invalid face-embedding generation") from error


def rank_selected_direct(
    search: SelfieSearch, query: object, *, reader: Reader, comparison_evidence: bool = False
) -> DirectRankingOutcome:
    if reader == "legacy" and requires_native_reader(search.configuration):
        raise RankingError("vector-only generation requires the native reader")
    ranker = rank_vector_direct if reader == "pgvector" else rank_legacy_direct
    try:
        outcome = ranker(search, query, comparison_evidence=comparison_evidence)
    except RankingError:
        raise
    except (MemoryError, ValueError) as error:
        raise RankingError("compatible cohort could not be ranked") from error
    emit_selfie_event(
        logger,
        event=SelfieEventName.DIRECT_READER_FINISHED,
        event_id=search.event_id,
        search_id=search.pk,
        reader=reader,
        eligible_face_count=outcome.eligible_face_count,
        eligible_photo_count=outcome.eligible_photo_count,
        matched_photo_count=len(outcome.photos),
        ranking_ms=min(MAX_BOUNDED_INTEGER, max(0, round(outcome.ranking_ms))),
    )
    return outcome
