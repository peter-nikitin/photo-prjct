"""Store independently reported results and consented selfie uploads."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from selfie_search.images import PreparedSelfie
from selfie_search.models import (
    FEEDBACK_CONSENT_TEXT_VERSION,
    SelfieSearch,
    SelfieSearchFeedback,
)
from selfie_search.services.results import saved_ready_result_page
from selfie_search.storage import FeedbackSelfieStorage


@transaction.atomic
@sensitive_variables()
def save_feedback_selfie(
    *,
    search_id: UUID,
    upload: PreparedSelfie,
    contact: str,
    storage: FeedbackSelfieStorage,
    paid_watermarked_previews_enabled: bool,
) -> bool:
    """Return whether a new consented selfie was accepted."""

    search = SelfieSearch.objects.select_for_update().get(pk=search_id)
    if SelfieSearchFeedback.objects.filter(search=search).exists():
        return False

    visible_count = saved_ready_result_page(
        search=search,
        page_number=None,
        paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
    ).paginator.count
    stored = storage.put(content=upload.content, content_type=upload.content_type)
    try:
        feedback = SelfieSearchFeedback(
            search=search,
            variant=(
                SelfieSearchFeedback.Variant.SELFIE
                if visible_count
                else SelfieSearchFeedback.Variant.PROBLEM
            ),
            contact=contact,
            personal_data_consent=True,
            consent_text_version=FEEDBACK_CONSENT_TEXT_VERSION,
            consented_at=timezone.now(),
            source_status=search.status,
            source_matched_photo_count=search.matched_photo_count,
            source_visible_result_count=visible_count,
            source_configuration=search.configuration,
            object_key=stored.key,
            object_content_type=stored.content_type,
            object_size=stored.size,
            object_uploaded_at=timezone.now(),
        )
        feedback.full_clean()
        feedback.save(force_insert=True)
    except Exception:
        try:
            storage.delete(key=stored.key)
        except Exception:  # The private bucket lifecycle still bounds retention.
            pass
        raise
    return True
