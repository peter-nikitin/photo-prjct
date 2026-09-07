from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from typing import Protocol
from uuid import UUID

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone
from picflow.models import Photo
from PIL import Image
from processing.services.enrollment import GENERATE_PREVIEW_CONFIGURATION

from ingestion.models import UploadItem
from ingestion.services.batches import (
    ItemStateConflict,
    _derive_and_save,
    _locked_item,
    _locked_owned_batch,
    classify_failure,
)
from ingestion.services.publication import (
    OriginalVerificationError,
    VerifiedOriginal,
    publish_photo,
    verify_jpeg_object,
    verify_object_identity,
)
from ingestion.storage import (
    ObjectChanged,
    ObjectIdentity,
    ObjectMismatch,
    ObjectMissing,
    StorageError,
)


class ConfirmationStorage(Protocol):
    def inspect(self, *, key: str) -> ObjectIdentity: ...

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes: ...

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity: ...

    def delete(self, *, key: str) -> None: ...


Failpoint = Callable[[str], None]


class _VerificationFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def confirm_upload_item(
    *,
    uploader: AbstractBaseUser,
    batch_id: UUID,
    item_id: UUID,
    storage: ConfirmationStorage,
    failpoint: Failpoint | None = None,
) -> Photo | None:
    """Verify and persist one private JPEG, safely converging after interruption."""
    checkpoint = ""
    source_etag_wire: str | None = None
    recovering_final = False
    incoming_key = ""

    try:
        with transaction.atomic():
            batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
            item = _locked_item(batch=batch, item_id=item_id)
            if item.status == UploadItem.Status.UPLOADED:
                existing_photo = _uploaded_photo(item)
                incoming_key = item.incoming_key
            else:
                existing_photo = None
                _require_authorized(item)

                recovering_final = _inspect_recoverable_final(storage=storage, item=item)
                if recovering_final:
                    checkpoint = item.verified_source_etag or ""
                else:
                    try:
                        source = verify_jpeg_object(
                            storage=storage,
                            key=item.incoming_key,
                            expected_size=item.expected_size,
                        )
                    except OriginalVerificationError as error:
                        raise _VerificationFailure(error.code) from None

                    checkpoint = source.etag_value
                    source_etag_wire = source.etag_wire
                    item.verified_source_etag = checkpoint
                    item.error_code = ""
                    item.sanitized_error_message = ""
                    item.last_activity_at = timezone.now()
                    item.save(
                        update_fields=[
                            "verified_source_etag",
                            "error_code",
                            "sanitized_error_message",
                            "last_activity_at",
                        ]
                    )
                    batch.last_activity_at = item.last_activity_at
                    batch.save(update_fields=["last_activity_at"])

        if existing_photo is not None:
            _best_effort_delete(storage=storage, incoming_key=incoming_key)
            return existing_photo

        _run_failpoint(failpoint, "after_source_checkpoint")

        # Storage promotion and lazy JPEG-header inspection are deliberately outside the
        # authoritative mutation transaction. The final transaction below revalidates the
        # persisted checkpoint and item state before publishing any database state.
        if not recovering_final:
            if source_etag_wire is None:
                raise _VerificationFailure("promotion_conflict")
            storage.promote(
                incoming_key=item.incoming_key,
                final_key=item.final_key,
                etag_wire=source_etag_wire,
            )
            _run_failpoint(failpoint, "after_copy")

        final = storage.inspect(key=item.final_key)
        _require_final_identity(item=item, identity=final, checkpoint=checkpoint)
        _run_failpoint(failpoint, "after_final_head")
        preview_first = bool(getattr(settings, "PHOTO_PROCESSING_PREVIEW_ENABLED", False))
        preview_geometry = (
            _oriented_jpeg_geometry(
                storage=storage,
                key=item.final_key,
                etag_wire=final.etag_wire,
                byte_size=final.size,
            )
            if preview_first
            else None
        )
        original = VerifiedOriginal(
            key=item.final_key,
            identity=final,
            filename=item.original_filename,
            oriented_geometry=preview_geometry,
        )
        _run_failpoint(failpoint, "after_preview_geometry")

        with transaction.atomic():
            batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
            item = _locked_item(batch=batch, item_id=item_id)
            if item.status == UploadItem.Status.UPLOADED:
                photo = _uploaded_photo(item)
                incoming_key = item.incoming_key
                completed_concurrently = True
            else:
                completed_concurrently = False
                _require_authorized(item)
                if not checkpoint or item.verified_source_etag != checkpoint:
                    raise ObjectChanged()
                if item.folder_id is not None and item.folder.event_id != batch.event_id:
                    raise ItemStateConflict(
                        "folder_event_mismatch", "The upload folder does not belong to this event."
                    )

                photo = publish_photo(
                    photo_id=item.id.hex,
                    uploader=uploader,
                    event=batch.event,
                    folder=item.folder,
                    original=original,
                )
                now = timezone.now()
                item.photo = photo
                item.status = UploadItem.Status.UPLOADED
                item.error_code = ""
                item.sanitized_error_message = ""
                item.authorization_expires_at = None
                item.completed_at = now
                item.last_activity_at = now
                item.save(
                    update_fields=[
                        "photo",
                        "status",
                        "error_code",
                        "sanitized_error_message",
                        "authorization_expires_at",
                        "completed_at",
                        "last_activity_at",
                    ]
                )
                batch.last_activity_at = now
                _derive_and_save(batch=batch, require_terminal=False)

        if completed_concurrently:
            _best_effort_delete(storage=storage, incoming_key=incoming_key)
            return photo

        _run_failpoint(failpoint, "after_photo_commit")
        _run_failpoint(failpoint, "before_incoming_delete")
        _best_effort_delete(storage=storage, incoming_key=item.incoming_key)
        return photo
    except _VerificationFailure as failure:
        return _record_failure(
            uploader=uploader,
            batch_id=batch_id,
            item_id=item_id,
            failure=failure.code,
        )
    except StorageError as failure:
        return _record_failure(
            uploader=uploader,
            batch_id=batch_id,
            item_id=item_id,
            failure=failure,
        )


def _inspect_recoverable_final(*, storage: ConfirmationStorage, item: UploadItem) -> bool:
    try:
        final = storage.inspect(key=item.final_key)
    except ObjectMissing:
        return False
    if not item.verified_source_etag:
        raise _VerificationFailure("promotion_conflict")
    _require_final_identity(
        item=item,
        identity=final,
        checkpoint=item.verified_source_etag,
    )
    return True


def _require_final_identity(*, item: UploadItem, identity: ObjectIdentity, checkpoint: str) -> None:
    try:
        verify_object_identity(
            identity=identity,
            expected_size=item.expected_size,
            expected_etag=checkpoint,
            expected_content_type=item.declared_content_type,
        )
    except OriginalVerificationError:
        raise ObjectMismatch() from None


def _require_authorized(item: UploadItem) -> None:
    if item.status == UploadItem.Status.FAILED:
        raise ItemStateConflict("item_failed", "A failed item must be retried explicitly.")
    if item.status != UploadItem.Status.AUTHORIZED:
        raise ItemStateConflict("item_not_authorized", "Authorize the item before confirmation.")


def _uploaded_photo(item: UploadItem) -> Photo:
    if item.photo_id is None:
        raise ItemStateConflict("uploaded_item_missing_photo", "The uploaded item is inconsistent.")
    return item.photo


def _record_failure(
    *,
    uploader: AbstractBaseUser,
    batch_id: UUID,
    item_id: UUID,
    failure: object,
) -> Photo | None:
    disposition = classify_failure(failure)
    messages = {
        "object_changed": "The uploaded object changed. Upload the file again.",
        "object_missing": "The uploaded object is not available yet.",
        "storage_unavailable": "Object storage is temporarily unavailable.",
        "size_mismatch": "The uploaded file size does not match.",
        "content_type_mismatch": "The uploaded file type does not match.",
        "invalid_jpeg": "The uploaded file is not a valid JPEG.",
        "promotion_conflict": "The stored photo does not match the verified upload.",
        "internal_error": "The upload could not be confirmed.",
    }
    now = timezone.now()
    with transaction.atomic():
        batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
        item = _locked_item(batch=batch, item_id=item_id)
        if item.status == UploadItem.Status.UPLOADED:
            return _uploaded_photo(item)
        _require_authorized(item)
        item.error_code = disposition.code
        item.sanitized_error_message = messages[disposition.code]
        item.last_activity_at = now
        update_fields = ["error_code", "sanitized_error_message", "last_activity_at"]
        if not disposition.retryable:
            item.status = UploadItem.Status.FAILED
            item.authorization_expires_at = None
            item.completed_at = now
            update_fields.extend(["status", "authorization_expires_at", "completed_at"])
        item.save(update_fields=update_fields)
        batch.last_activity_at = now
        _derive_and_save(batch=batch, require_terminal=False)
    return None


def _run_failpoint(failpoint: Failpoint | None, checkpoint: str) -> None:
    if failpoint is not None:
        failpoint(checkpoint)


def _best_effort_delete(*, storage: ConfirmationStorage, incoming_key: str) -> None:
    try:
        storage.delete(key=incoming_key)
    except StorageError:
        pass


def _oriented_jpeg_geometry(
    *, storage: ConfirmationStorage, key: str, etag_wire: str, byte_size: int
) -> tuple[int, int]:
    """Parse bounded JPEG headers/EXIF without decoding or materializing the pixel buffer."""
    header_limit = min(byte_size, 1024 * 1024)
    chunk_size = 64 * 1024
    prefix = bytearray()
    width = height = 0
    orientation = 1
    for start in range(0, header_limit, chunk_size):
        end = min(header_limit, start + chunk_size) - 1
        prefix.extend(storage.read_range(key=key, etag_wire=etag_wire, start=start, end=end))
        try:
            with Image.open(BytesIO(prefix)) as source:
                width, height = source.size
                orientation = int(source.getexif().get(274, 1))
            break
        except Image.DecompressionBombError:
            raise _VerificationFailure("invalid_jpeg") from None
        except (OSError, ValueError):
            if end + 1 >= header_limit:
                raise _VerificationFailure("invalid_jpeg") from None
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    worker = GENERATE_PREVIEW_CONFIGURATION["worker"]
    assert isinstance(worker, dict)
    max_pixels = worker["max_pixels"]
    assert isinstance(max_pixels, int)
    if width < 1 or height < 1 or width * height > max_pixels:
        raise _VerificationFailure("invalid_jpeg")
    return width, height
