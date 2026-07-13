from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone
from picflow.models import Photo

from ingestion.models import UploadItem
from ingestion.services.batches import (
    ItemStateConflict,
    _derive_and_save,
    _locked_item,
    _locked_owned_batch,
    classify_failure,
)
from ingestion.storage import ObjectIdentity, ObjectMismatch, ObjectMissing, StorageError


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

    try:
        with transaction.atomic():
            batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
            item = _locked_item(batch=batch, item_id=item_id)
            if item.status == UploadItem.Status.UPLOADED:
                return _uploaded_photo(item)
            _require_authorized(item)

            recovering_final = _inspect_recoverable_final(storage=storage, item=item)
            if recovering_final:
                checkpoint = item.verified_source_etag or ""
            else:
                source = storage.inspect(key=item.incoming_key)
                _require_source_metadata(item=item, identity=source)
                first = storage.read_range(
                    key=item.incoming_key,
                    etag_wire=source.etag_wire,
                    start=0,
                    end=1,
                )
                last = storage.read_range(
                    key=item.incoming_key,
                    etag_wire=source.etag_wire,
                    start=item.expected_size - 2,
                    end=item.expected_size - 1,
                )
                if first != b"\xff\xd8" or last != b"\xff\xd9":
                    raise _VerificationFailure("invalid_jpeg")

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

        _run_failpoint(failpoint, "after_source_checkpoint")

        with transaction.atomic():
            batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
            item = _locked_item(batch=batch, item_id=item_id)
            if item.status == UploadItem.Status.UPLOADED:
                return _uploaded_photo(item)
            _require_authorized(item)
            if not checkpoint or item.verified_source_etag != checkpoint:
                raise _VerificationFailure("promotion_conflict")

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

            now = timezone.now()
            photo = Photo.objects.create(
                id=item.id.hex,
                event=batch.event,
                src="",
                uploaded_by=batch.uploader,
                original_key=item.final_key,
                original_filename=item.original_filename,
                original_size=item.expected_size,
                original_content_type=item.declared_content_type,
                uploaded_at=now,
            )
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

        _run_failpoint(failpoint, "after_photo_commit")
        _run_failpoint(failpoint, "before_incoming_delete")
        try:
            storage.delete(key=item.incoming_key)
        except StorageError:
            pass
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


def _require_source_metadata(*, item: UploadItem, identity: ObjectIdentity) -> None:
    if identity.size != item.expected_size:
        raise _VerificationFailure("size_mismatch")
    if identity.content_type != item.declared_content_type:
        raise _VerificationFailure("content_type_mismatch")


def _require_final_identity(*, item: UploadItem, identity: ObjectIdentity, checkpoint: str) -> None:
    if (
        identity.etag_value != checkpoint
        or identity.size != item.expected_size
        or identity.content_type != item.declared_content_type
    ):
        raise ObjectMismatch()


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
