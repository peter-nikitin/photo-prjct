"""Native direct ranking and its shared search observability."""

import logging

from selfie_search.models import SelfieSearch
from selfie_search.observability import MAX_BOUNDED_INTEGER, SelfieEventName, emit_selfie_event
from selfie_search.services.direct_ranking import DirectRankingOutcome
from selfie_search.services.ranking import RankingError
from selfie_search.services.vector_ranking import rank_vector_direct

logger = logging.getLogger(__name__)


def rank_selected_direct(search: SelfieSearch, query: object) -> DirectRankingOutcome:
    try:
        outcome = rank_vector_direct(search, query)
    except RankingError:
        raise
    except (MemoryError, ValueError) as error:
        raise RankingError("compatible cohort could not be ranked") from error
    emit_selfie_event(
        logger,
        event=SelfieEventName.DIRECT_READER_FINISHED,
        event_id=search.event_id,
        search_id=search.pk,
        reader="pgvector",
        eligible_face_count=outcome.eligible_face_count,
        eligible_photo_count=outcome.eligible_photo_count,
        matched_photo_count=len(outcome.photos),
        ranking_ms=min(MAX_BOUNDED_INTEGER, max(0, round(outcome.ranking_ms))),
    )
    return outcome
