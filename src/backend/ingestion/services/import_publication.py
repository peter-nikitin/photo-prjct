from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ingestion.models import ImportedContent, ImportItem, ImportScope
from ingestion.services.imports import (
    ImportConflict,
    _derive_batch,
    _finish_attempt,
    _locked_active_attempt,
    _persist_callback_transition,
    _require_batch_eligible,
)
from ingestion.services.publication import (
    OriginalVerificationError,
    VerifiedOriginal,
    publish_photo,
    verify_jpeg_object,
    verify_object_identity,
)
from ingestion.storage import ObjectIdentity, ObjectMissing, StorageError


class ImportPublicationStorage(Protocol):
    def inspect(self, *, key: str) -> ObjectIdentity: ...

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes: ...

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity: ...

    def delete(self, *, key: str) -> None: ...


@dataclass(frozen=True)
class ImportCompletion:
    status: str
    item_id: UUID
    photo_id: str | None


@dataclass(frozen=True)
class _UploadCheckpoint:
    incoming_key: str
    final_key: str
    content_sha256: str
    byte_size: int
    source_etag: str
    final_etag: str


def complete_import_item(
    *,
    attempt_id: UUID,
    item_id: UUID,
    storage: ImportPublicationStorage,
    clock: Callable[[], datetime] = timezone.now,
) -> ImportCompletion:
    """Converge one attempt-owned upload on exactly one scope publication."""
    existing = ImportItem.objects.filter(pk=item_id).only("id", "status", "photo_id").first()
    if existing is None:
        raise ImportItem.DoesNotExist
    if existing.status in {ImportItem.Status.IMPORTED, ImportItem.Status.DUPLICATE}:
        return ImportCompletion(existing.status, existing.id, existing.photo_id)

    with _persist_callback_transition(
        attempt_id=attempt_id,
        item_id=item_id,
        clock=clock,
    ):
        checkpoint = _load_checkpoint(
            attempt_id=attempt_id,
            item_id=item_id,
            clock=clock,
        )
        try:
            final = _recover_final(storage=storage, checkpoint=checkpoint)
            if final is None:
                source = verify_jpeg_object(
                    storage=storage,
                    key=checkpoint.incoming_key,
                    expected_size=checkpoint.byte_size,
                )
                _save_source_checkpoint(
                    attempt_id=attempt_id,
                    item_id=item_id,
                    source=source,
                    clock=clock,
                )
                storage.promote(
                    incoming_key=checkpoint.incoming_key,
                    final_key=checkpoint.final_key,
                    etag_wire=source.etag_wire,
                )
                final = storage.inspect(key=checkpoint.final_key)
                verify_object_identity(
                    identity=final,
                    expected_size=checkpoint.byte_size,
                    expected_etag=source.etag_value,
                )
            _save_final_checkpoint(
                attempt_id=attempt_id,
                item_id=item_id,
                final=final,
                clock=clock,
            )
        except (OriginalVerificationError, StorageError) as error:
            code = error.code if hasattr(error, "code") else "storage_unavailable"
            raise ImportConflict(code, "The uploaded object could not be verified.") from None

        result, duplicate = _publish_checkpointed_item(
            attempt_id=attempt_id,
            item_id=item_id,
            final=final,
            clock=clock,
        )

    _best_effort_delete(storage, checkpoint.incoming_key)
    if duplicate:
        _best_effort_delete(storage, checkpoint.final_key)
    return result


def _load_checkpoint(
    *,
    attempt_id: UUID,
    item_id: UUID,
    clock: Callable[[], datetime],
) -> _UploadCheckpoint:
    with transaction.atomic():
        item = ImportItem.objects.select_for_update().get(pk=item_id)
        batch, attempt = _locked_active_attempt(
            batch_id=item.batch_id,
            attempt_id=attempt_id,
            item_id=item.id,
            kind="file",
            clock=clock,
        )
        _require_batch_eligible(batch)
        if item.status != ImportItem.Status.UPLOADING:
            raise ImportConflict("item_not_uploaded", "The import item has no prepared upload.")
        if (
            not attempt.incoming_key
            or not attempt.final_key
            or not attempt.content_sha256
            or attempt.byte_size is None
        ):
            raise ImportConflict(
                "missing_checkpoint", "The import upload checkpoint is incomplete."
            )
        return _UploadCheckpoint(
            incoming_key=attempt.incoming_key,
            final_key=attempt.final_key,
            content_sha256=attempt.content_sha256,
            byte_size=attempt.byte_size,
            source_etag=attempt.verified_source_etag,
            final_etag=attempt.verified_final_etag,
        )


def _recover_final(
    *,
    storage: ImportPublicationStorage,
    checkpoint: _UploadCheckpoint,
) -> ObjectIdentity | None:
    expected_etag = checkpoint.final_etag or checkpoint.source_etag
    if not expected_etag:
        return None
    try:
        final = storage.inspect(key=checkpoint.final_key)
    except ObjectMissing:
        if checkpoint.final_etag:
            raise
        return None
    verify_object_identity(
        identity=final,
        expected_size=checkpoint.byte_size,
        expected_etag=expected_etag,
    )
    return final


def _save_source_checkpoint(
    *,
    attempt_id: UUID,
    item_id: UUID,
    source: ObjectIdentity,
    clock: Callable[[], datetime],
) -> None:
    with transaction.atomic():
        item = ImportItem.objects.select_for_update().get(pk=item_id)
        batch, attempt = _locked_active_attempt(
            batch_id=item.batch_id,
            attempt_id=attempt_id,
            item_id=item.id,
            kind="file",
            clock=clock,
        )
        _require_batch_eligible(batch)
        if item.status != ImportItem.Status.UPLOADING:
            raise ImportConflict("item_not_uploaded", "The import item state changed.")
        if attempt.verified_source_etag not in {"", source.etag_value}:
            raise ImportConflict("source_changed", "The incoming object changed.")
        attempt.verified_source_etag = source.etag_value
        attempt.save(update_fields=["verified_source_etag"])


def _save_final_checkpoint(
    *,
    attempt_id: UUID,
    item_id: UUID,
    final: ObjectIdentity,
    clock: Callable[[], datetime],
) -> None:
    with transaction.atomic():
        item = ImportItem.objects.select_for_update().get(pk=item_id)
        batch, attempt = _locked_active_attempt(
            batch_id=item.batch_id,
            attempt_id=attempt_id,
            item_id=item.id,
            kind="file",
            clock=clock,
        )
        _require_batch_eligible(batch)
        if item.status != ImportItem.Status.UPLOADING:
            raise ImportConflict("item_not_uploaded", "The import item state changed.")
        expected_etag = attempt.verified_final_etag or attempt.verified_source_etag
        if not expected_etag:
            raise ImportConflict("missing_checkpoint", "The source checkpoint is missing.")
        try:
            verify_object_identity(
                identity=final,
                expected_size=attempt.byte_size or 0,
                expected_etag=expected_etag,
            )
        except OriginalVerificationError:
            raise ImportConflict("promotion_conflict", "The final object changed.") from None
        attempt.verified_final_etag = final.etag_value
        attempt.save(update_fields=["verified_final_etag"])


def _publish_checkpointed_item(
    *,
    attempt_id: UUID,
    item_id: UUID,
    final: ObjectIdentity,
    clock: Callable[[], datetime],
) -> tuple[ImportCompletion, bool]:
    duplicate = False
    with transaction.atomic():
        item = (
            ImportItem.objects.select_for_update(of=("self",))
            .select_related("batch__owner", "batch__event", "batch__folder")
            .get(pk=item_id)
        )
        if item.status in {ImportItem.Status.IMPORTED, ImportItem.Status.DUPLICATE}:
            return ImportCompletion(item.status, item.id, item.photo_id), False
        batch, attempt = _locked_active_attempt(
            batch_id=item.batch_id,
            attempt_id=attempt_id,
            item_id=item.id,
            kind="file",
            clock=clock,
        )
        _require_batch_eligible(batch)
        if batch.scope_id is None:
            raise ImportConflict("manifest_incomplete", "The canonical import scope is missing.")
        if (
            not attempt.content_sha256
            or attempt.byte_size is None
            or not attempt.final_key
            or not attempt.verified_final_etag
        ):
            raise ImportConflict("missing_checkpoint", "The final checkpoint is incomplete.")
        try:
            verify_object_identity(
                identity=final,
                expected_size=attempt.byte_size,
                expected_etag=attempt.verified_final_etag,
            )
        except OriginalVerificationError:
            raise ImportConflict("promotion_conflict", "The final object changed.") from None

        ImportScope.objects.select_for_update().get(pk=batch.scope_id)
        content = ImportedContent.objects.filter(
            scope_id=batch.scope_id,
            sha256=attempt.content_sha256,
            byte_size=attempt.byte_size,
        ).first()
        if content is not None:
            duplicate = True
            photo = content.photo
            item.status = ImportItem.Status.DUPLICATE
        else:
            original = VerifiedOriginal(
                key=attempt.final_key,
                identity=final,
                filename=item.filename,
                oriented_geometry=(attempt.oriented_width, attempt.oriented_height)
                if attempt.oriented_width is not None and attempt.oriented_height is not None
                else None,
            )
            photo = publish_photo(
                photo_id=item.id.hex,
                uploader=batch.owner,
                event=batch.event,
                folder=batch.folder,
                original=original,
            )
            ImportedContent.objects.create(
                scope_id=batch.scope_id,
                sha256=attempt.content_sha256,
                byte_size=attempt.byte_size,
                photo=photo,
                source_item=item,
            )
            item.status = ImportItem.Status.IMPORTED
        completed_at = clock()
        if attempt.lease_expires_at <= completed_at:
            raise ImportConflict("stale_attempt", "The import lease is no longer current.")
        item.content_sha256 = attempt.content_sha256
        item.photo = photo
        item.error_code = ""
        item.completed_at = completed_at
        item.save(update_fields=["content_sha256", "status", "photo", "error_code", "completed_at"])
        _finish_attempt(attempt, "succeeded", now=completed_at)
        _derive_batch(batch, now=completed_at)
        result = ImportCompletion(item.status, item.id, photo.id)
    return result, duplicate


def _best_effort_delete(storage: ImportPublicationStorage, key: str) -> None:
    try:
        storage.delete(key=key)
    except StorageError:
        pass
