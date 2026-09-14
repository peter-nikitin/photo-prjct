from __future__ import annotations

from collections.abc import Collection
from typing import Final, Protocol

from django.db.models import F
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
)

from picflow.models import Photo

GALLERY_PREVIEW_URL_TTL_SECONDS: Final = 21_600


class AcceptedPreviewSigner(Protocol):
    def sign_accepted_preview(self, *, key: str, expires_in: int) -> str: ...


def issue_gallery_preview_urls(
    *, photos: Collection[Photo], signer: AcceptedPreviewSigner
) -> dict[str, str]:
    """Issue direct small-preview URLs for an already authorized gallery page."""
    photo_list = list(photos)
    if len({photo.pk for photo in photo_list}) != len(photo_list):
        raise ValueError("gallery page contains duplicate photo identities")

    expected = {
        photo.pk: selection
        for photo in photo_list
        if (selection := _expected_derivative(photo)) is not None
    }
    if not expected:
        return {}

    derivatives = PhotoDerivative.objects.filter(
        photo_id__in=expected,
        variant__in={variant for _, variant in expected.values()},
        accepted_attempt__accepted=True,
        accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
        photo__processing_states__status=PhotoProcessingState.Status.SUCCEEDED,
        photo__processing_states__processor_type=F("accepted_attempt__processor_type"),
        photo__processing_states__accepted_attempt=F("accepted_attempt"),
    ).select_related("accepted_attempt")

    selected: dict[str, PhotoDerivative] = {}
    for derivative in derivatives:
        expected_processor, expected_variant = expected.get(derivative.photo_id, (None, None))
        if (
            derivative.variant != expected_variant
            or derivative.accepted_attempt.processor_type != expected_processor
            or derivative.photo_id in selected
        ):
            raise ValueError("gallery preview evidence is inconsistent")
        selected[derivative.photo_id] = derivative

    if set(selected) != set(expected):
        raise ValueError("gallery preview evidence is incomplete")

    return {
        photo.pk: signer.sign_accepted_preview(
            key=selected[photo.pk].final_key,
            expires_in=GALLERY_PREVIEW_URL_TTL_SECONDS,
        )
        for photo in photo_list
        if photo.pk in selected
    }


def _expected_derivative(photo: Photo) -> tuple[str, str] | None:
    if photo.gallery_media_policy == Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED:
        return None
    if photo.gallery_media_policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED:
        return GENERATE_PREVIEW_PROCESSOR, "preview-small-v1"
    if photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED:
        return GENERATE_WATERMARKED_PREVIEW_PROCESSOR, "preview-watermarked-v1"
    raise ValueError("gallery preview policy is invalid")
