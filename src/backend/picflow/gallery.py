import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol, Self

from django.db.models import F, Q, QuerySet
from django.urls import reverse
from ingestion.storage import ObjectMismatch, ObjectMissing, OpenedObject, ReadableBody
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
)

from picflow.models import Event, Photo
from picflow.pagination import SignedCursor

GalleryVariant = Literal["preview-small", "preview-large"]
GALLERY_VARIANTS: frozenset[GalleryVariant] = frozenset({"preview-small", "preview-large"})
MediaUrlBuilder = Callable[[Photo, GalleryVariant], str]
DownloadUrlBuilder = Callable[[Photo], str]
GALLERY_PAGE_SIZE: Final = 50

logger = logging.getLogger(__name__)


class FinalObjectStorage(Protocol):
    def open_final(self, *, key: str) -> OpenedObject: ...

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str: ...


@dataclass(frozen=True)
class GalleryMedia:
    url: str
    variant: GalleryVariant


@dataclass(frozen=True)
class GalleryPhoto:
    photo_id: str
    preview_media_small: GalleryMedia
    preview_media_large: GalleryMedia
    download_url: str
    alt: str


@dataclass(frozen=True)
class GalleryPage:
    photos: tuple[Photo, ...]
    next_cursor: str | None


class GalleryPhotoFactory:
    @staticmethod
    def from_photo(
        *,
        photo: Photo,
        event_slug: str,
        media_url_builder: MediaUrlBuilder | None = None,
        download_url_builder: DownloadUrlBuilder | None = None,
    ) -> GalleryPhoto:
        def media(variant: GalleryVariant) -> GalleryMedia:
            return GalleryMedia(
                url=(
                    media_url_builder(photo, variant)
                    if media_url_builder is not None
                    else reverse(
                        "photo_media",
                        kwargs={"slug": event_slug, "photo_id": photo.pk, "variant": variant},
                    )
                ),
                variant=variant,
            )

        return GalleryPhoto(
            photo_id=photo.pk,
            preview_media_small=media("preview-small"),
            preview_media_large=media("preview-large"),
            download_url=(
                download_url_builder(photo)
                if download_url_builder is not None
                else reverse(
                    "photo_download",
                    kwargs={"slug": event_slug, "photo_id": photo.pk},
                )
            ),
            alt=f"Фото {photo.pk} с события {photo.event.name}",
        )


def gallery_photo_queryset(*, event: Event) -> QuerySet[Photo]:
    """Return database-confirmed gallery media without probing object storage."""
    preview_ready = Q(
        gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        derivatives__variant="preview-small-v1",
        processing_states__processor_type=GENERATE_PREVIEW_PROCESSOR,
        processing_states__status=PhotoProcessingState.Status.SUCCEEDED,
        processing_states__accepted_attempt=F("derivatives__accepted_attempt"),
        processing_states__accepted_attempt__accepted=True,
        processing_states__accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
    )
    return (
        Photo.objects.filter(event=event, src="", original_key__isnull=False)
        .filter(
            Q(gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED) | preview_ready
        )
        .select_related("event")
        .order_by("id")
    )


def gallery_page(
    *, event: Event, cursor: str | None, signer: SignedCursor | None = None
) -> GalleryPage:
    signer = signer or SignedCursor()
    collection = f"normal-gallery:{event.pk}"
    photos = gallery_photo_queryset(event=event)
    if cursor is not None:
        photos = photos.filter(pk__gt=signer.decode(cursor=cursor, collection=collection))
    page_with_sentinel = tuple(photos[: GALLERY_PAGE_SIZE + 1])
    page_photos = page_with_sentinel[:GALLERY_PAGE_SIZE]
    next_cursor = (
        signer.encode(collection=collection, last_key=page_photos[-1].pk)
        if len(page_with_sentinel) > GALLERY_PAGE_SIZE
        else None
    )
    return GalleryPage(photos=page_photos, next_cursor=next_cursor)


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
        key = self._selected_key(photo=photo, variant=variant)
        try:
            opened = self._storage.open_final(key=key)
        except ValueError:
            raise ObjectMismatch() from None
        extension: Literal["jpg", "png"] = "jpg" if opened.content_type == "image/jpeg" else "png"
        return ResolvedPublicMedia(
            body=opened.body,
            content_length=opened.size,
            content_type=opened.content_type,
            extension=extension,
        )

    def resolve_signed(self, *, photo: Photo, variant: GalleryVariant) -> str:
        key = self._selected_key(photo=photo, variant=variant)
        try:
            return self._storage.sign_final(key=key)
        except ValueError:
            raise ObjectMismatch() from None

    def resolve_download(self, *, photo: Photo) -> str:
        if not photo.original_key or photo.original_content_type not in {
            "image/jpeg",
            "image/png",
        }:
            raise ValueError("ineligible original download")
        extension: Literal["jpg", "png"] = (
            "jpg" if photo.original_content_type == "image/jpeg" else "png"
        )
        try:
            return self._storage.sign_final(
                key=photo.original_key,
                attachment_filename=f"findme-photo-{photo.pk}.{extension}",
            )
        except ValueError:
            raise ObjectMismatch() from None

    @staticmethod
    def _selected_key(*, photo: Photo, variant: GalleryVariant) -> str:
        if variant not in GALLERY_VARIANTS or not photo.original_key:
            raise ValueError("ineligible gallery media")
        if (
            photo.gallery_media_policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
            and variant == "preview-small"
        ):
            try:
                return PhotoDerivative.objects.get(
                    photo=photo, variant="preview-small-v1"
                ).final_key
            except PhotoDerivative.DoesNotExist:
                raise ObjectMissing() from None
        return photo.original_key


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
