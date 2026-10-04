from typing import cast

from django.contrib.auth.base_user import AbstractBaseUser
from feature_flags import services as feature_flag_services
from feature_flags.registry import PAID_WATERMARKED_PREVIEWS

from picflow.models import Event, Photo


def policy_for_new_photo(
    event: Event,
    user: AbstractBaseUser,
) -> tuple[str, str]:
    if event.access_type == Event.AccessType.PAID and feature_flag_services.is_enabled(
        PAID_WATERMARKED_PREVIEWS,
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
