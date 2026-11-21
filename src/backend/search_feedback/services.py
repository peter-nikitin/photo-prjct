from uuid import UUID

from django.db.models import QuerySet
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone
from selfie_search.models import SelfieSearch

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


def face_result_reports(search: SelfieSearch) -> QuerySet[SearchFeedback]:
    return SearchFeedback.objects.alias(
        root_search_id=KeyTextTransform("search_id", "search_context")
    ).filter(
        event_id=search.event_id,
        search_type__in=(SearchFeedback.SearchType.SELFIE, SearchFeedback.SearchType.GALLERY),
        root_search_id=str(search.pk),
    )


def active_negative_reports(search: SelfieSearch) -> QuerySet[SearchFeedback]:
    return face_result_reports(search).filter(
        value=SearchFeedback.Value.ABSENT, withdrawn_at__isnull=True
    )


def set_result_report(
    *,
    search: SelfieSearch,
    photo_id: str,
    submission_id: UUID,
    search_type: SearchFeedback.SearchType,
    search_context: dict[str, str],
    mark: bool,
) -> bool:
    """Journal each action under the caller's root-search lock."""
    if face_result_reports(search).filter(submission_id=submission_id).exists():
        return False

    active = active_negative_reports(search).filter(photo_id=photo_id)
    now = timezone.now()
    changed = not active.exists() if mark else bool(active.update(withdrawn_at=now))
    closed = not mark or not changed
    SearchFeedback.objects.create(
        submission_id=submission_id,
        event_id=search.event_id,
        photo_id=photo_id,
        search_type=search_type,
        value=SearchFeedback.Value.ABSENT if mark else SearchFeedback.Value.UNMARK,
        search_context=search_context,
        withdrawn_at=now if closed else None,
    )
    return changed
