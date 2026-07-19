from dataclasses import dataclass
from typing import Literal

from django.urls import reverse

from picflow.models import Photo

GalleryVariant = Literal["preview-small", "preview-large"]


@dataclass(frozen=True)
class GalleryMedia:
    url: str
    variant: GalleryVariant


@dataclass(frozen=True)
class GalleryPhoto:
    photo_id: str
    preview_media_small: GalleryMedia
    preview_media_large: GalleryMedia
    alt: str


class GalleryPhotoFactory:
    @staticmethod
    def from_photo(*, photo: Photo, event_slug: str) -> GalleryPhoto:
        def media(variant: GalleryVariant) -> GalleryMedia:
            return GalleryMedia(
                url=reverse(
                    "photo_media",
                    kwargs={"slug": event_slug, "photo_id": photo.pk, "variant": variant},
                ),
                variant=variant,
            )

        return GalleryPhoto(
            photo_id=photo.pk,
            preview_media_small=media("preview-small"),
            preview_media_large=media("preview-large"),
            alt=f"Фото {photo.pk} с события {photo.event.name}",
        )
