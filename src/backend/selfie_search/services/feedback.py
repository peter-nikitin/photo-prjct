"""Server-authoritative submission of consented selfie-search feedback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from selfie_search.forms import validate_selfie_upload
from selfie_search.models import (
    FEEDBACK_CONSENT_TEXT_VERSION,
    SelfieSearch,
    SelfieSearchFeedback,
    SelfieSearchFeedbackLabel,
    SelfieSearchResult,
)
from selfie_search.services.results import saved_ready_result_photos
from selfie_search.storage import FeedbackSelfieStorage


class FeedbackSubmissionError(Exception):
    """Base class for intentionally non-sensitive public submission outcomes."""


class FeedbackInvalid(FeedbackSubmissionError):
    pass


class FeedbackNonTerminal(FeedbackSubmissionError):
    pass


class FeedbackResultChanged(FeedbackSubmissionError):
    pass


@dataclass(frozen=True)
class FeedbackPresentation:
    variant: str
    visible_result_count: int
    visible_result_ids: frozenset[UUID]


@dataclass(frozen=True)
class FeedbackSubmission:
    feedback: SelfieSearchFeedback
    created: bool


def feedback_result_source(result: SelfieSearchResult) -> str:
    """Return the immutable server-derived source used for feedback reporting.

    Customer input contains only a label value.  The source is resolved from the saved result
    primary source and immutable cluster evidence, so a submitted label can never choose or
    mutate provenance.
    """
    if result.primary_source == SelfieSearchResult.PrimarySource.DIRECT:
        if result.cluster_evidence.exists():
            return "dual_evidence"
        return "direct"
    return "expanded"


def feedback_presentation(search: SelfieSearch) -> FeedbackPresentation:
    """Derive the feedback variant from the same current public-result membership as the page."""
    if search.status not in _TERMINAL_SEARCH_STATUSES:
        raise FeedbackNonTerminal
    if search.status != SelfieSearch.Status.READY:
        return FeedbackPresentation(
            variant=cast(str, SelfieSearchFeedback.Variant.PROBLEM),
            visible_result_count=0,
            visible_result_ids=frozenset(),
        )

    visible_photo_ids = tuple(photo.pk for photo in saved_ready_result_photos(search))
    visible_result_ids = frozenset(
        SelfieSearchResult.objects.filter(
            search=search, photo_id__in=visible_photo_ids
        ).values_list("pk", flat=True)
    )
    variant = cast(
        str,
        (
            SelfieSearchFeedback.Variant.RESULT_LABELS
            if visible_result_ids
            else SelfieSearchFeedback.Variant.PROBLEM
        ),
    )
    return FeedbackPresentation(
        variant=variant,
        visible_result_count=len(visible_result_ids),
        visible_result_ids=visible_result_ids,
    )


def submit_search_feedback(
    *,
    search_id: UUID,
    upload,
    contact: str,
    labels: dict[str, str],
    storage: FeedbackSelfieStorage,
) -> FeedbackSubmission:
    """Store one immutable feedback record or return the already accepted submission."""
    with transaction.atomic():
        search = SelfieSearch.objects.select_for_update().get(pk=search_id)
        existing = SelfieSearchFeedback.objects.filter(search=search).first()
        if existing is not None:
            return FeedbackSubmission(feedback=existing, created=False)

        upload = validate_selfie_upload(upload)
        presentation = feedback_presentation(search)
        label_values = _validate_labels(
            search=search,
            labels=labels,
            presentation=presentation,
        )
        content = upload.read()
        upload.seek(0)
        stored = storage.put(content=content, content_type=upload.content_type)
        try:
            with transaction.atomic():
                feedback = SelfieSearchFeedback(
                    search=search,
                    variant=presentation.variant,
                    contact=contact,
                    personal_data_consent=True,
                    consent_text_version=FEEDBACK_CONSENT_TEXT_VERSION,
                    consented_at=timezone.now(),
                    source_status=search.status,
                    source_matched_photo_count=search.matched_photo_count,
                    source_visible_result_count=presentation.visible_result_count,
                    source_configuration=search.configuration,
                    object_key=stored.key,
                    object_content_type=stored.content_type,
                    object_size=stored.size,
                    object_uploaded_at=timezone.now(),
                )
                feedback.full_clean()
                feedback.save(force_insert=True)
                if label_values:
                    result_map = {
                        result.pk: result
                        for result in SelfieSearchResult.objects.filter(pk__in=label_values)
                    }
                    for result_id, value in label_values.items():
                        label = SelfieSearchFeedbackLabel(
                            feedback=feedback,
                            result=result_map[result_id],
                            value=value,
                        )
                        label.full_clean()
                        label.save(force_insert=True)
        except IntegrityError:
            _delete_uploaded_object(storage=storage, key=stored.key)
            existing = SelfieSearchFeedback.objects.filter(search=search).first()
            if existing is not None:
                return FeedbackSubmission(feedback=existing, created=False)
            raise
        except Exception:
            _delete_uploaded_object(storage=storage, key=stored.key)
            raise
    return FeedbackSubmission(feedback=feedback, created=True)


def _validate_labels(
    *,
    search: SelfieSearch,
    labels: dict[str, str],
    presentation: FeedbackPresentation,
) -> dict[UUID, str]:
    if not isinstance(labels, dict):
        raise FeedbackInvalid
    parsed = _parse_label_ids(labels)
    if presentation.variant == SelfieSearchFeedback.Variant.PROBLEM:
        if parsed:
            if search.status == SelfieSearch.Status.READY and _labels_belong_to_search(
                search=search, result_ids=frozenset(parsed)
            ):
                raise FeedbackResultChanged
            raise FeedbackInvalid
        return {}
    current_ids = presentation.visible_result_ids
    requested_ids = frozenset(parsed)
    if requested_ids.issubset(current_ids):
        return parsed
    if _labels_belong_to_search(search=search, result_ids=requested_ids):
        raise FeedbackResultChanged
    raise FeedbackInvalid


def _parse_label_ids(labels: dict[str, str]) -> dict[UUID, str]:
    parsed: dict[UUID, str] = {}
    for raw_result_id, value in labels.items():
        if (
            not isinstance(raw_result_id, str)
            or not isinstance(value, str)
            or value
            not in {
                SelfieSearchFeedbackLabel.Value.PRESENT,
                SelfieSearchFeedbackLabel.Value.ABSENT,
            }
        ):
            raise FeedbackInvalid
        try:
            result_id = UUID(raw_result_id)
        except ValueError:
            raise FeedbackInvalid from None
        if result_id in parsed:
            raise FeedbackInvalid
        parsed[result_id] = value
    return parsed


def _delete_uploaded_object(*, storage: FeedbackSelfieStorage, key: str) -> None:
    try:
        storage.delete(key=key)
    except Exception:  # An orphan remains private and bounded by the bucket lifecycle.
        pass


def _labels_belong_to_search(*, search: SelfieSearch, result_ids: frozenset[UUID]) -> bool:
    search_result_ids = frozenset(
        SelfieSearchResult.objects.filter(search=search, pk__in=result_ids).values_list(
            "pk", flat=True
        )
    )
    return result_ids.issubset(search_result_ids)


_TERMINAL_SEARCH_STATUSES = frozenset(
    {
        SelfieSearch.Status.READY,
        SelfieSearch.Status.NO_FACE,
        SelfieSearch.Status.MULTIPLE_FACES,
        SelfieSearch.Status.QUALITY_REJECTED,
        SelfieSearch.Status.SEARCH_UNAVAILABLE,
        SelfieSearch.Status.FAILED,
    }
)
