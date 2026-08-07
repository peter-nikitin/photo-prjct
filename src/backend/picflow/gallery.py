import logging
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol, Self

from django.core.paginator import Page, Paginator
from django.db.models import Case, DateTimeField, F, Q, QuerySet, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.urls import reverse
from ingestion.storage import ObjectMismatch, ObjectMissing, OpenedObject, ReadableBody
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.models import Event, Photo

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
    download_url: str
    alt: str
    faces: tuple[GalleryFaceCrop, ...] = ()


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
                download_url_builder(photo)
                if download_url_builder is not None
                else reverse(
                    "photo_download",
                    kwargs={"slug": event_slug, "photo_id": photo.pk},
                )
            ),
            alt=f"Фото {photo.pk} с события {photo.event.name}",
            faces=faces,
        )


def gallery_photo_queryset(
    *,
    event: Event,
    capture_time_start=None,
    capture_time_end=None,
) -> QuerySet[Photo]:
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
    queryset = (
        Photo.objects.filter(event=event, src="", original_key__isnull=False)
        .filter(
            Q(gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED) | preview_ready
        )
        .select_related("event")
        .order_by("original_filename", "id")
        .distinct()
    )
    if capture_time_start is None and capture_time_end is None:
        return queryset
    if capture_time_start is None or capture_time_end is None:
        raise ValueError("capture time bounds must be supplied together")
    capture_time = KeyTextTransform("capture_time", "processing_states__accepted_attempt__result")
    canonical_capture_time = Case(
        When(
            processing_states__accepted_attempt__result__capture_time__regex=(
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
            ),
            then=Cast(capture_time, DateTimeField()),
        ),
        default=None,
        output_field=DateTimeField(),
    )
    return (
        queryset.filter(
            Q(processing_states__current_run=F("processing_states__current_attempt__run")),
            Q(processing_states__current_job=F("processing_states__current_attempt__job")),
            Q(processing_states__current_run=F("processing_states__accepted_attempt__run")),
            Q(processing_states__current_job=F("processing_states__accepted_attempt__job")),
            processing_states__processor_type=CAPTURE_METADATA_PROCESSOR,
            processing_states__status=PhotoProcessingState.Status.SUCCEEDED,
            processing_states__current_run__processor_type=CAPTURE_METADATA_PROCESSOR,
            processing_states__current_run__processor_version=2,
            processing_states__current_job__processor_type=CAPTURE_METADATA_PROCESSOR,
            processing_states__current_job__processor_version=2,
            processing_states__current_job__status=ProcessingJob.Status.SUCCEEDED,
            processing_states__current_attempt=F("processing_states__accepted_attempt"),
            processing_states__accepted_attempt__processor_type=CAPTURE_METADATA_PROCESSOR,
            processing_states__accepted_attempt__processor_version=2,
            processing_states__accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
            processing_states__accepted_attempt__accepted=True,
        )
        .annotate(capture_time=canonical_capture_time)
        .filter(capture_time__gte=capture_time_start, capture_time__lte=capture_time_end)
    )


def gallery_page(
    *,
    event: Event,
    page_number: str | None,
    capture_time_start=None,
    capture_time_end=None,
) -> Page[Photo]:
    return Paginator(
        gallery_photo_queryset(
            event=event,
            capture_time_start=capture_time_start,
            capture_time_end=capture_time_end,
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
