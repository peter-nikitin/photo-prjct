from uuid import UUID

from search_feedback.models import SearchFeedback


def record_result_report(
    *,
    submission_id: UUID,
    event_id: int,
    photo_id: str,
    search_type: SearchFeedback.SearchType,
    search_context: dict[str, str],
) -> None:
    SearchFeedback.objects.create(
        submission_id=submission_id,
        event_id=event_id,
        photo_id=photo_id,
        search_type=search_type,
        value=SearchFeedback.Value.ABSENT,
        search_context=search_context,
    )
