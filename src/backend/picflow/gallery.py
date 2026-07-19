import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol, Self

from django.urls import reverse
from ingestion.storage import ObjectMismatch, OpenedObject, ReadableBody

from picflow.models import Photo

GalleryVariant = Literal["preview-small", "preview-large"]
GALLERY_VARIANTS: frozenset[GalleryVariant] = frozenset({"preview-small", "preview-large"})

logger = logging.getLogger(__name__)


class FinalObjectStorage(Protocol):
    def open_final(self, *, key: str) -> OpenedObject: ...


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


@dataclass(frozen=True)
class ResolvedPublicMedia:
    body: ReadableBody
    content_length: int
    content_type: Literal["image/jpeg", "image/png"]
    extension: Literal["jpg", "png"]


class PublicMediaResolver:
    def __init__(self, storage: FinalObjectStorage) -> None:
        self._storage = storage

    def resolve(self, *, photo: Photo, variant: GalleryVariant) -> ResolvedPublicMedia:
        if variant not in GALLERY_VARIANTS or not photo.original_key:
            raise ValueError("ineligible gallery media")
        try:
            opened = self._storage.open_final(key=photo.original_key)
        except ValueError:
            raise ObjectMismatch() from None
        extension: Literal["jpg", "png"] = "jpg" if opened.content_type == "image/jpeg" else "png"
        return ResolvedPublicMedia(
            body=opened.body,
            content_length=opened.size,
            content_type=opened.content_type,
            extension=extension,
        )


class CloseableMediaIterator(Iterator[bytes]):
    def __init__(
        self,
        *,
        media: ResolvedPublicMedia,
        event_slug: str,
        photo_id: str,
        chunk_size: int = 65536,
    ) -> None:
        self._body = media.body
        self._event_slug = event_slug
        self._photo_id = photo_id
        self._chunk_size = chunk_size
        self._closed = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            chunk = self._body.read(self._chunk_size)
        except Exception:
            logger.error(
                "Public photo stream ended early",
                extra={"event_slug": self._event_slug, "photo_id": self._photo_id},
            )
            self.close()
            raise StopIteration from None
        if chunk == b"":
            self.close()
            raise StopIteration
        return chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._body.close()
