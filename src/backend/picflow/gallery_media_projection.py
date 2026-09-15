"""Atomic publication of accepted derivatives into gallery-facing media slots."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
)

from picflow.models import GalleryMediaProjection


class GalleryMediaProjectionConflict(ValueError):
    """A gallery-media slot already contains different accepted evidence."""


@dataclass(frozen=True)
class _ProjectionSlot:
    final_key_field: str
    source_attempt_field: str
    processor_type: str


_SLOTS = {
    "preview-small-v1": _ProjectionSlot(
        final_key_field="clean_preview_final_key",
        source_attempt_field="clean_preview_source_attempt",
        processor_type=GENERATE_PREVIEW_PROCESSOR,
    ),
    "preview-watermarked-v1": _ProjectionSlot(
        final_key_field="watermarked_preview_final_key",
        source_attempt_field="watermarked_preview_source_attempt",
        processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    ),
}


def publish_gallery_media(derivative: PhotoDerivative) -> GalleryMediaProjection:
    """Monotonically publish one current accepted preview derivative."""
    slot = _SLOTS.get(derivative.variant)
    if slot is None:
        raise ValueError("gallery-media projection requires a supported preview derivative")
    if derivative.pk is None:
        raise ValueError("gallery-media projection requires a published derivative")

    supplied_attempt = derivative.accepted_attempt
    if not (
        supplied_attempt.photo_id == derivative.photo_id
        and supplied_attempt.processor_type == slot.processor_type
        and supplied_attempt.status == ProcessingAttempt.Status.SUCCEEDED
        and supplied_attempt.accepted
    ):
        raise ValueError(
            "gallery-media projection requires the matching accepted successful producer"
        )

    with transaction.atomic():
        stored = PhotoDerivative.objects.select_related("accepted_attempt").get(pk=derivative.pk)
        if (
            stored.photo_id != derivative.photo_id
            or stored.variant != derivative.variant
            or stored.final_key != derivative.final_key
            or stored.accepted_attempt_id != derivative.accepted_attempt_id
        ):
            raise ValueError("gallery-media projection requires immutable derivative evidence")

        attempt = stored.accepted_attempt
        state = PhotoProcessingState.objects.filter(
            photo_id=stored.photo_id,
            processor_type=slot.processor_type,
        ).first()
        if not (
            attempt.photo_id == stored.photo_id
            and attempt.processor_type == slot.processor_type
            and attempt.status == ProcessingAttempt.Status.SUCCEEDED
            and attempt.accepted
            and state is not None
            and state.status == PhotoProcessingState.Status.SUCCEEDED
            and state.current_attempt_id == attempt.id
            and state.accepted_attempt_id == attempt.id
        ):
            raise ValueError(
                "gallery-media projection requires the current accepted successful producer"
            )

        projection = (
            GalleryMediaProjection.objects.select_for_update()
            .filter(photo_id=stored.photo_id)
            .first()
        )
        if projection is None:
            projection = GalleryMediaProjection.objects.create(photo_id=stored.photo_id)

        current = (
            getattr(projection, slot.final_key_field),
            getattr(projection, f"{slot.source_attempt_field}_id"),
        )
        published = (stored.final_key, stored.accepted_attempt_id)
        if current == published:
            return projection
        if current != (None, None):
            raise GalleryMediaProjectionConflict(
                "gallery-media projection slot already contains different accepted evidence"
            )

        setattr(projection, slot.final_key_field, stored.final_key)
        setattr(projection, slot.source_attempt_field, attempt)
        projection.save(
            update_fields=[slot.final_key_field, slot.source_attempt_field, "updated_at"]
        )
        return projection
