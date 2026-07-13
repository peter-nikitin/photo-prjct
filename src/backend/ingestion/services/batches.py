from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone
from picflow.models import Event

from ingestion.models import UploadBatch, UploadItem
from ingestion.storage import (
    ObjectChanged,
    ObjectMissing,
    StorageError,
    StorageUnavailable,
    UploadGrant,
)

MAX_BATCH_ITEMS = 10_000
MAX_REGISTRATION_CHUNK = 100
MAX_UPLOAD_ATTEMPTS = 4


class AuthorizationReason(StrEnum):
    DATA_ATTEMPT = "data_attempt"
    GRANT_REFRESH = "grant_refresh"


class BatchServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class BatchConflict(BatchServiceError):
    """A batch request conflicts with immutable data or current state."""


class ItemStateConflict(BatchServiceError):
    """An item transition is invalid in its current state."""


@dataclass(frozen=True)
class BatchInput:
    expected_item_count: int


@dataclass(frozen=True)
class ItemInput:
    client_item_id: UUID
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True)
class BatchResult:
    id: UUID
    status: str
    expected: int
    uploaded: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ItemResult:
    id: UUID
    client_item_id: UUID
    status: str
    attempt: int
    error_code: str


@dataclass(frozen=True)
class RegistrationResult:
    items: tuple[ItemResult, ...]
    created: bool


@dataclass(frozen=True)
class AuthorizationResult:
    item: ItemResult
    grant: UploadGrant


@dataclass(frozen=True)
class FailureDisposition:
    code: str
    retryable: bool
    action: AuthorizationReason | None


class UploadStorage(Protocol):
    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant: ...


def create_batch(*, uploader: AbstractBaseUser, event: Event, data: BatchInput) -> BatchResult:
    count = data.expected_item_count
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_BATCH_ITEMS:
        raise BatchConflict("invalid_expected_item_count", "Select between 1 and 10,000 files.")
    with transaction.atomic():
        batch = UploadBatch.objects.create(
            uploader=uploader,
            event=event,
            expected_item_count=count,
        )
    return _batch_result(batch)


def register_items(
    *,
    uploader: AbstractBaseUser,
    batch_id: UUID,
    items: Sequence[ItemInput],
) -> RegistrationResult:
    if not 1 <= len(items) <= MAX_REGISTRATION_CHUNK:
        raise BatchConflict("invalid_registration_chunk", "Register between 1 and 100 files.")
    _validate_item_inputs(items)

    with transaction.atomic():
        batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
        if batch.status == UploadBatch.Status.COMPLETED:
            raise BatchConflict("batch_completed", "The upload batch is already completed.")

        client_ids = [item.client_item_id for item in items]
        existing = {
            row.client_item_id: row
            for row in UploadItem.objects.filter(batch=batch, client_item_id__in=client_ids)
        }
        new_count = sum(item.client_item_id not in existing for item in items)
        persisted_count = UploadItem.objects.filter(batch=batch).count()
        if persisted_count + new_count > batch.expected_item_count:
            raise BatchConflict(
                "expected_item_count_exceeded",
                "Registration exceeds the batch file count.",
            )

        now = timezone.now()
        results: list[ItemResult] = []
        for item_input in items:
            row = existing.get(item_input.client_item_id)
            if row is not None:
                if not _metadata_matches(row, item_input):
                    raise BatchConflict(
                        "item_metadata_conflict",
                        "This file identifier is already registered with different metadata.",
                    )
            else:
                item_id = uuid4()
                row = UploadItem.objects.create(
                    id=item_id,
                    batch=batch,
                    client_item_id=item_input.client_item_id,
                    original_filename=item_input.filename,
                    declared_content_type=item_input.content_type,
                    expected_size=item_input.size,
                    incoming_key=f"incoming/{batch.id}/{item_id}",
                    final_key=f"originals/{item_id.hex}",
                    last_activity_at=now,
                )
            results.append(_item_result(row))

        batch.last_activity_at = now
        batch.save(update_fields=["last_activity_at"])
        return RegistrationResult(tuple(results), created=new_count == len(items))


def authorize_item(
    *,
    uploader: AbstractBaseUser,
    batch_id: UUID,
    item_id: UUID,
    reason: AuthorizationReason,
    storage: UploadStorage,
) -> AuthorizationResult:
    try:
        normalized_reason = AuthorizationReason(reason)
    except ValueError:
        raise ItemStateConflict(
            "invalid_authorization_reason", "Invalid authorization reason."
        ) from None

    with transaction.atomic():
        batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
        item = _locked_item(batch=batch, item_id=item_id)
        return _authorize_locked(
            batch=batch,
            item=item,
            reason=normalized_reason,
            storage=storage,
        )


def manual_retry_item(
    *,
    uploader: AbstractBaseUser,
    batch_id: UUID,
    item_id: UUID,
    storage: UploadStorage,
) -> AuthorizationResult:
    with transaction.atomic():
        batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
        item = _locked_item(batch=batch, item_id=item_id)
        if item.status != UploadItem.Status.FAILED:
            raise ItemStateConflict("item_not_failed", "Only a failed upload can be retried.")

        now = timezone.now()
        item.status = UploadItem.Status.PENDING
        item.error_code = ""
        item.sanitized_error_message = ""
        item.authorization_expires_at = None
        item.upload_attempts = 0
        item.last_activity_at = now
        item.save(
            update_fields=[
                "status",
                "error_code",
                "sanitized_error_message",
                "authorization_expires_at",
                "upload_attempts",
                "last_activity_at",
            ]
        )
        batch.status = UploadBatch.Status.UPLOADING
        batch.completed_at = None
        batch.last_activity_at = now
        batch.save(update_fields=["status", "completed_at", "last_activity_at"])
        return _authorize_locked(
            batch=batch,
            item=item,
            reason=AuthorizationReason.DATA_ATTEMPT,
            storage=storage,
        )


def report_item_failed(
    *,
    uploader: AbstractBaseUser,
    batch_id: UUID,
    item_id: UUID,
    code: str,
) -> ItemResult:
    public_messages = {
        "transfer_retries_exhausted": "The upload could not be completed after several attempts.",
        "transfer_cancelled": "The upload was cancelled.",
    }
    if code not in public_messages:
        raise ItemStateConflict("invalid_failure_code", "Invalid upload failure code.")

    with transaction.atomic():
        batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
        item = _locked_item(batch=batch, item_id=item_id)
        if item.status == UploadItem.Status.UPLOADED:
            raise ItemStateConflict("item_uploaded", "An uploaded item cannot be changed.")
        if item.status == UploadItem.Status.FAILED:
            if item.error_code == code:
                return _item_result(item)
            raise ItemStateConflict("item_failed", "The item already has a terminal failure.")

        now = timezone.now()
        item.status = UploadItem.Status.FAILED
        item.error_code = code
        item.sanitized_error_message = public_messages[code]
        item.authorization_expires_at = None
        item.last_activity_at = now
        item.save(
            update_fields=[
                "status",
                "error_code",
                "sanitized_error_message",
                "authorization_expires_at",
                "last_activity_at",
            ]
        )
        batch.last_activity_at = now
        _derive_and_save(batch=batch, require_terminal=False)
        return _item_result(item)


def derive_batch_status(
    *, uploader: AbstractBaseUser, batch_id: UUID, finalize: bool = False
) -> BatchResult:
    with transaction.atomic():
        batch = _locked_owned_batch(uploader=uploader, batch_id=batch_id)
        return _derive_and_save(batch=batch, require_terminal=finalize)


def classify_failure(failure: object) -> FailureDisposition:
    terminal_codes = {
        "size_mismatch",
        "content_type_mismatch",
        "invalid_jpeg",
        "promotion_conflict",
        "transfer_retries_exhausted",
        "transfer_cancelled",
        "upload_expired",
    }
    if isinstance(failure, str) and failure in terminal_codes:
        return FailureDisposition(failure, False, None)
    if isinstance(failure, ObjectChanged) or (isinstance(failure, int) and failure in {409, 412}):
        return FailureDisposition("object_changed", True, AuthorizationReason.DATA_ATTEMPT)
    if failure == 403:
        return FailureDisposition("storage_unavailable", True, AuthorizationReason.GRANT_REFRESH)
    if isinstance(failure, (TimeoutError, ConnectionError, StorageUnavailable)):
        return FailureDisposition("storage_unavailable", True, AuthorizationReason.DATA_ATTEMPT)
    if isinstance(failure, ObjectMissing):
        return FailureDisposition("object_missing", True, AuthorizationReason.DATA_ATTEMPT)
    if isinstance(failure, int) and (failure in {408, 429} or 500 <= failure <= 599):
        return FailureDisposition("storage_unavailable", True, AuthorizationReason.DATA_ATTEMPT)
    if isinstance(failure, StorageError):
        return FailureDisposition("storage_unavailable", True, AuthorizationReason.DATA_ATTEMPT)
    return FailureDisposition("internal_error", False, None)


def _authorize_locked(
    *,
    batch: UploadBatch,
    item: UploadItem,
    reason: AuthorizationReason,
    storage: UploadStorage,
) -> AuthorizationResult:
    if batch.status == UploadBatch.Status.COMPLETED:
        raise BatchConflict("batch_completed", "The upload batch is already completed.")
    if item.status == UploadItem.Status.UPLOADED:
        raise ItemStateConflict("item_uploaded", "An uploaded item cannot be authorized.")
    if item.status == UploadItem.Status.FAILED:
        raise ItemStateConflict("item_failed", "Retry the failed item before authorization.")

    if reason == AuthorizationReason.GRANT_REFRESH:
        if item.status != UploadItem.Status.AUTHORIZED or item.upload_attempts < 1:
            raise ItemStateConflict(
                "no_active_upload_attempt", "There is no active upload authorization to refresh."
            )
    elif item.upload_attempts >= MAX_UPLOAD_ATTEMPTS:
        raise ItemStateConflict(
            "transfer_retries_exhausted", "No automatic upload attempts remain."
        )

    grant = storage.create_presigned_post(
        incoming_key=item.incoming_key,
        max_bytes=item.expected_size,
    )
    now = timezone.now()
    if reason == AuthorizationReason.DATA_ATTEMPT:
        item.upload_attempts += 1
    item.status = UploadItem.Status.AUTHORIZED
    item.authorization_expires_at = grant.expires_at
    item.last_activity_at = now
    item.save(
        update_fields=[
            "upload_attempts",
            "status",
            "authorization_expires_at",
            "last_activity_at",
        ]
    )
    batch.status = UploadBatch.Status.UPLOADING
    batch.completed_at = None
    batch.last_activity_at = now
    batch.save(update_fields=["status", "completed_at", "last_activity_at"])
    return AuthorizationResult(_item_result(item), grant)


def _derive_and_save(*, batch: UploadBatch, require_terminal: bool) -> BatchResult:
    counts = {
        status: UploadItem.objects.filter(batch=batch, status=status).count()
        for status in UploadItem.Status.values
    }
    persisted = sum(counts.values())
    if require_terminal and persisted != batch.expected_item_count:
        raise BatchConflict("missing_registrations", "Not every selected file is registered.")
    active = counts[UploadItem.Status.PENDING] + counts[UploadItem.Status.AUTHORIZED]
    if require_terminal and active:
        raise BatchConflict("items_not_terminal", "Some uploads are still active.")

    uploaded = counts[UploadItem.Status.UPLOADED]
    failed = counts[UploadItem.Status.FAILED]
    status = batch.status
    if persisted == batch.expected_item_count and active == 0:
        if uploaded == batch.expected_item_count:
            status = UploadBatch.Status.COMPLETED
        elif uploaded and failed:
            status = UploadBatch.Status.PARTIAL
        elif failed == batch.expected_item_count:
            status = UploadBatch.Status.FAILED
    elif (
        status
        in {
            UploadBatch.Status.UPLOADING,
            UploadBatch.Status.PARTIAL,
            UploadBatch.Status.FAILED,
        }
        and active
    ):
        status = UploadBatch.Status.UPLOADING

    now = timezone.now()
    batch.status = status
    batch.completed_at = now if status == UploadBatch.Status.COMPLETED else None
    batch.last_activity_at = now
    batch.save(update_fields=["status", "completed_at", "last_activity_at"])
    return _batch_result(batch, uploaded=uploaded, failed=failed)


def _locked_owned_batch(*, uploader: AbstractBaseUser, batch_id: UUID) -> UploadBatch:
    return UploadBatch.objects.select_for_update().get(id=batch_id, uploader_id=uploader.pk)


def _locked_item(*, batch: UploadBatch, item_id: UUID) -> UploadItem:
    return UploadItem.objects.select_for_update().get(id=item_id, batch=batch)


def _validate_item_inputs(items: Sequence[ItemInput]) -> None:
    seen: set[UUID] = set()
    for item in items:
        if not isinstance(item.client_item_id, UUID) or item.client_item_id in seen:
            raise BatchConflict("invalid_client_item_id", "File identifiers must be unique UUIDs.")
        seen.add(item.client_item_id)
        if not item.filename or len(item.filename) > 255:
            raise BatchConflict("invalid_filename", "Filename is required.")
        if item.content_type != "image/jpeg":
            raise BatchConflict("invalid_content_type", "Only JPEG files can be uploaded.")
        if (
            isinstance(item.size, bool)
            or not isinstance(item.size, int)
            or not 1 <= item.size <= 52_428_800
        ):
            raise BatchConflict("invalid_size", "The JPEG file size is outside the allowed range.")


def _metadata_matches(row: UploadItem, item: ItemInput) -> bool:
    return (
        row.original_filename == item.filename
        and row.declared_content_type == item.content_type
        and row.expected_size == item.size
    )


def _batch_result(batch: UploadBatch, *, uploaded: int = 0, failed: int = 0) -> BatchResult:
    return BatchResult(batch.id, batch.status, batch.expected_item_count, uploaded, failed)


def _item_result(item: UploadItem) -> ItemResult:
    return ItemResult(
        item.id,
        item.client_item_id,
        item.status,
        item.upload_attempts,
        item.error_code,
    )
