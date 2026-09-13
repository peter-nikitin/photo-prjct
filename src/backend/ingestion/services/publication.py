from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone
from picflow.models import Event, EventFolder, Photo
from picflow.photo_policy import policy_for_new_photo
from processing.services.enrollment import request_capture_metadata, request_generate_preview

from ingestion.storage import ObjectIdentity

logger = logging.getLogger(__name__)


class OriginalVerificationStorage(Protocol):
    def inspect(self, *, key: str) -> ObjectIdentity: ...

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes: ...


class OriginalVerificationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedOriginal:
    key: str
    identity: ObjectIdentity
    filename: str
    oriented_geometry: tuple[int, int] | None = None


def verify_jpeg_object(
    *, storage: OriginalVerificationStorage, key: str, expected_size: int
) -> ObjectIdentity:
    """Verify immutable-object metadata and the JPEG boundary bytes."""
    identity = storage.inspect(key=key)
    verify_object_identity(identity=identity, expected_size=expected_size)
    if expected_size < 4:
        raise OriginalVerificationError("invalid_jpeg")
    first = storage.read_range(key=key, etag_wire=identity.etag_wire, start=0, end=1)
    last = storage.read_range(
        key=key,
        etag_wire=identity.etag_wire,
        start=expected_size - 2,
        end=expected_size - 1,
    )
    if first != b"\xff\xd8" or last != b"\xff\xd9":
        raise OriginalVerificationError("invalid_jpeg")
    return identity


def verify_object_identity(
    *,
    identity: ObjectIdentity,
    expected_size: int,
    expected_etag: str | None = None,
    expected_content_type: str = "image/jpeg",
) -> None:
    """Verify metadata for a previously checkpointed immutable object."""
    if identity.size != expected_size:
        raise OriginalVerificationError("size_mismatch")
    if identity.content_type != expected_content_type:
        raise OriginalVerificationError("content_type_mismatch")
    if expected_etag is not None and identity.etag_value != expected_etag:
        raise OriginalVerificationError("promotion_conflict")


def publish_photo(
    *,
    photo_id: str,
    uploader: AbstractBaseUser,
    event: Event,
    folder: EventFolder | None,
    original: VerifiedOriginal,
) -> Photo:
    """Publish a verified original and enroll processing in the caller's transaction."""
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    processing_generation, gallery_media_policy = policy_for_new_photo(locked_event, uploader)
    bib_processing_policy = (
        Photo.BibProcessingPolicy.ORIGINAL_V1
        if locked_event.bib_search_enabled
        else Photo.BibProcessingPolicy.DISABLED
    )
    photo = Photo.objects.create(
        id=photo_id,
        event=locked_event,
        folder=folder,
        src="",
        uploaded_by=uploader,
        original_key=original.key,
        original_filename=original.filename,
        original_size=original.identity.size,
        original_content_type=original.identity.content_type,
        uploaded_at=timezone.now(),
        processing_generation=processing_generation,
        gallery_media_policy=gallery_media_policy,
        bib_processing_policy=bib_processing_policy,
    )
    if locked_event.timezone_name is not None:
        request_capture_metadata(
            photo,
            verified_source_etag=original.identity.etag_value,
        )
    if original.oriented_geometry is not None:
        request_generate_preview(
            photo,
            pixel_width=original.oriented_geometry[0],
            pixel_height=original.oriented_geometry[1],
            verified_source_etag=original.identity.etag_value,
        )
    if (
        photo.bib_processing_policy == Photo.BibProcessingPolicy.ORIGINAL_V1
        and photo.processing_generation == Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1
    ):
        transaction.on_commit(
            lambda: _request_bib_after_publication(
                photo.pk, verified_source_etag=original.identity.etag_value
            )
        )
    return photo


def _request_bib_after_publication(
    photo_id: str, *, verified_source_etag: str | None = None
) -> None:
    """Best-effort downstream enqueue after the gallery publication commits."""
    from processing.services.enrollment import request_bib_recognition

    try:
        request_bib_recognition(
            Photo.objects.get(pk=photo_id), verified_source_etag=verified_source_etag
        )
    except Exception:
        logger.exception(
            "bib enrollment failed after photo publication", extra={"photo_id": photo_id}
        )
