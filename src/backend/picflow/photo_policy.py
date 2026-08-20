from typing import cast

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from feature_flags import services as feature_flag_services

from picflow.models import Event, Photo

PAID_WATERMARKED_PREVIEWS_FLAG = "paid-watermarked-previews"


def policy_for_new_photo(
    event: Event,
    user: AbstractBaseUser,
) -> tuple[str, str]:
    preview_first = bool(getattr(settings, "PHOTO_PROCESSING_PREVIEW_ENABLED", False))
    if not preview_first:
        return (
            cast(str, Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1),
            cast(str, Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED),
        )
    if event.access_type == Event.AccessType.PAID and feature_flag_services.is_enabled(
        PAID_WATERMARKED_PREVIEWS_FLAG,
        user,
    ):
        return (
            cast(str, Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1),
            cast(str, Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED),
        )
    return (
        cast(str, Photo.ProcessingGeneration.PREVIEW_FIRST_V1),
        cast(str, Photo.GalleryMediaPolicy.PREVIEW_REQUIRED),
    )
