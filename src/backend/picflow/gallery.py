import logging
import math
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol, Self, cast
from zoneinfo import ZoneInfo

from django.core.paginator import Page, Paginator
from django.db.models import F, Q, QuerySet
from django.db.models.functions import Lower
from django.urls import reverse
from ingestion.storage import ObjectMismatch, ObjectMissing, OpenedObject, ReadableBody
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
)

from picflow.models import Event, EventFolder, Photo

GalleryVariant = Literal["preview-small", "preview-large"]
GALLERY_VARIANTS: frozenset[GalleryVariant] = frozenset({"preview-small", "preview-large"})
MediaUrlBuilder = Callable[[Photo, GalleryVariant], str]
DownloadUrlBuilder = Callable[[Photo], str]
GALLERY_PAGE_SIZE: Final = 100

logger = logging.getLogger(__name__)


class FinalObjectStorage(Protocol):
    def open_final(self, *, key: str) -> OpenedObject: ...

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str: ...


@dataclass(frozen=True)
class GalleryMedia:
    url: str
    variant: GalleryVariant


@dataclass(frozen=True)
class GalleryFaceCrop:
    detection_id: str
    face_number: int
    left_percent: float
    top_percent: float
    size_percent: float
    search_url: str = ""


def gallery_face_crop(
    *, detection_id: str, face_index: int, geometry: object
) -> GalleryFaceCrop | None:
    """Return a padded square preview crop for one persisted face detection."""
    if isinstance(face_index, bool) or not isinstance(face_index, int) or face_index < 0:
        return None
    if not isinstance(geometry, dict) or geometry.get("coordinate_space") != "preview-small-v1":
        return None
    pixel_width = _finite_number(geometry.get("pixel_width"))
    pixel_height = _finite_number(geometry.get("pixel_height"))
    bbox = geometry.get("bbox")
    if pixel_width is None or pixel_height is None or pixel_width <= 0 or pixel_height <= 0:
        return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    x, y, width, height = (_finite_number(value) for value in bbox)
    if (
        x is None
        or y is None
        or width is None
        or height is None
        or width <= 0
        or height <= 0
        or x < 0
        or y < 0
        or x + width > pixel_width
        or y + height > pixel_height
    ):
        return None
    minimum_size = max(width, height)
    maximum_size = min(pixel_width, pixel_height)
    if minimum_size > maximum_size:
        return None
    size = min(minimum_size * 1.2, maximum_size)
    left = min(max(x + (width - size) / 2, 0), pixel_width - size)
    top = min(max(y + (height - size) / 2, 0), pixel_height - size)
    left_percent = left / pixel_width * 100
    top_percent = top / pixel_height * 100
    size_percent = size / pixel_width * 100
    values = (left_percent, top_percent, size_percent)
    if not all(math.isfinite(value) and 0 <= value <= 100 for value in values):
        return None
    return GalleryFaceCrop(
        detection_id=detection_id,
        face_number=face_index + 1,
        left_percent=left_percent,
        top_percent=top_percent,
        size_percent=size_percent,
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class GalleryPhoto:
    photo_id: str
    preview_media_small: GalleryMedia
    preview_media_large: GalleryMedia
    download_url: str | None
    alt: str
    faces: tuple[GalleryFaceCrop, ...] = ()
    capture_time_display: str | None = None


class GalleryPhotoFactory:
    @staticmethod
    def from_photo(
        *,
        photo: Photo,
        event_slug: str,
        media_url_builder: MediaUrlBuilder | None = None,
        download_url_builder: DownloadUrlBuilder | None = None,
        faces: tuple[GalleryFaceCrop, ...] = (),
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
                None
                if photo.gallery_media_policy
                == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
                else (
                    download_url_builder(photo)
                    if download_url_builder is not None
                    else reverse(
                        "photo_download",
                        kwargs={"slug": event_slug, "photo_id": photo.pk},
                    )
                )
            ),
            alt=f"Фото {photo.pk} с события {photo.event.name}",
            faces=faces,
            capture_time_display=(
                photo.capture_time.astimezone(ZoneInfo(photo.event.timezone_name)).strftime("%H:%M")
                if photo.capture_time is not None
                else None
            ),
        )


def gallery_photo_queryset(
    *,
    event: Event,
    capture_time_start=None,
    capture_time_end=None,
    folder_ids: Collection[int] | None = None,
    include_unfiled: bool = False,
    paid_watermarked_previews_enabled: bool = False,
) -> QuerySet[Photo]:
    """Return event-surface media without probing object storage."""
    if event.access_type == Event.AccessType.FREE:
        eligibility = _legacy_or_clean_preview_ready()
    elif paid_watermarked_previews_enabled:
        eligibility = _accepted_derivative_ready(
            policy=cast(str, Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED),
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
    else:
        eligibility = Q(pk__in=())
    queryset = (
        _public_photo_queryset(event=event)
        .filter(eligibility)
        .select_related("event")
        .order_by("original_filename", "id")
        .distinct()
    )
    if folder_ids or include_unfiled:
        folder_filter = Q()
        if folder_ids:
            folder_filter |= Q(folder_id__in=folder_ids)
        if include_unfiled:
            folder_filter |= Q(folder_id__isnull=True)
        queryset = queryset.filter(folder_filter)
    if capture_time_start is not None:
        queryset = queryset.filter(capture_time__gte=capture_time_start)
    if capture_time_end is not None:
        queryset = queryset.filter(capture_time__lte=capture_time_end)
    return queryset


def saved_result_photo_queryset(
    *,
    event: Event,
    paid_watermarked_previews_enabled: bool,
) -> QuerySet[Photo]:
    """Return saved-result presentation media under its compatibility contract."""
    eligibility = _legacy_or_clean_preview_ready()
    if paid_watermarked_previews_enabled:
        eligibility |= _accepted_derivative_ready(
            policy=cast(str, Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED),
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
    return _public_photo_queryset(event=event).filter(eligibility).distinct()


def _public_photo_queryset(*, event: Event) -> QuerySet[Photo]:
    return Photo.objects.filter(event=event, src="", original_key__isnull=False)


def _legacy_or_clean_preview_ready() -> Q:
    return Q(gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED) | (
        _accepted_derivative_ready(
            policy=cast(str, Photo.GalleryMediaPolicy.PREVIEW_REQUIRED),
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
    )


def _accepted_derivative_ready(*, policy: str, processor_type: str, variant: str) -> Q:
    return Q(
        gallery_media_policy=policy,
        derivatives__variant=variant,
        processing_states__processor_type=processor_type,
        processing_states__status=PhotoProcessingState.Status.SUCCEEDED,
        processing_states__accepted_attempt=F("derivatives__accepted_attempt"),
        processing_states__accepted_attempt__accepted=True,
        processing_states__accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
    )


def gallery_folder_choices(
    *, event: Event, base_queryset: QuerySet[Photo]
) -> tuple[tuple[EventFolder, ...], bool]:
    """Return folder controls derived only from the event's base public gallery."""
    folders = tuple(
        EventFolder.objects.filter(event=event, photos__in=base_queryset)
        .order_by(Lower("name"), "id")
        .distinct()
    )
    has_unfiled = base_queryset.filter(folder_id__isnull=True).exists()
    return folders, has_unfiled


def gallery_page(
    *,
    event: Event,
    page_number: str | None,
    capture_time_start=None,
    capture_time_end=None,
    folder_ids: Collection[int] | None = None,
    include_unfiled: bool = False,
    paid_watermarked_previews_enabled: bool = False,
) -> Page[Photo]:
    return Paginator(
        gallery_photo_queryset(
            event=event,
            capture_time_start=capture_time_start,
            capture_time_end=capture_time_end,
            folder_ids=folder_ids,
            include_unfiled=include_unfiled,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        ),
        GALLERY_PAGE_SIZE,
    ).page(page_number or 1)


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
        if (
            photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
            or not photo.original_key
            or photo.original_content_type
            not in {
                "image/jpeg",
                "image/png",
            }
        ):
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
        derivative_variant = None
        if photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED:
            derivative_variant = "preview-watermarked-v1"
        elif (
            photo.gallery_media_policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
            and variant == "preview-small"
        ):
            derivative_variant = "preview-small-v1"
        if derivative_variant is not None:
            try:
                return PhotoDerivative.objects.get(
                    photo=photo, variant=derivative_variant
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
