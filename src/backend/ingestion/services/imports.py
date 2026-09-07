from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone
from feature_flags.registry import YANDEX_DISK_IMPORT
from feature_flags.services import is_enabled
from picflow.models import Event, EventFolder

from ingestion.models import (
    ImportAttempt,
    ImportBatch,
    ImportedContent,
    ImportItem,
    ImportManifestPage,
    ImportScope,
)
from ingestion.storage import UploadGrant

MAX_MANIFEST_ENTRIES = 10_000
MAX_OPERATION_ATTEMPTS = 4
MAX_IMPORT_BYTES = 52_428_800
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MD5 = re.compile(r"[0-9a-f]{32}")


class ImportStorage(Protocol):
    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant: ...


class ImportConflict(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    name: str
    kind: Literal["jpeg", "directory", "unsupported"]
    size: int | None = None
    sha256: str | None = None
    md5: str | None = None
    version: str = ""


@dataclass(frozen=True)
class ImportResult:
    id: UUID
    status: str
    jpeg_count: int
    directory_count: int
    unsupported_count: int
    imported_count: int
    duplicate_count: int
    error_count: int
    manifest_attempts: int


@dataclass(frozen=True)
class ManifestPageResult:
    page_number: int
    replayed: bool


@dataclass(frozen=True)
class ClaimedImport:
    kind: Literal["empty", "manifest", "file"]
    batch_id: UUID | None = None
    item_id: UUID | None = None
    attempt_id: UUID | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class PreparedImportUpload:
    status: Literal["upload", "duplicate"]
    item_id: UUID
    grant: UploadGrant | None = None


def create_import(
    *,
    actor: AbstractBaseUser,
    event: Event,
    folder: EventFolder | None,
    submitted_source_key: str,
    submission_key: str,
) -> ImportResult:
    """Accept one import without contacting the source service."""
    owner = _fresh_eligible_owner(actor.pk)
    if folder is not None and folder.event_id != event.pk:
        raise ImportConflict("folder_event_mismatch", "The folder does not belong to this event.")
    if not submitted_source_key or len(submitted_source_key) > 512:
        raise ImportConflict("invalid_source", "The public source key is invalid.")
    if not submission_key or len(submission_key) > 128:
        raise ImportConflict("invalid_submission_key", "The submission key is invalid.")

    with transaction.atomic():
        batch, created = ImportBatch.objects.get_or_create(
            owner=owner,
            submission_key=submission_key,
            defaults={
                "event": event,
                "folder": folder,
                "submitted_source_key": submitted_source_key,
            },
        )
        if not created and (
            batch.event_id != event.pk
            or batch.folder_id != (folder.pk if folder is not None else None)
            or batch.submitted_source_key != submitted_source_key
        ):
            raise ImportConflict(
                "submission_conflict", "The submission key already identifies another import."
            )
    return _result(batch)


def record_manifest_page(
    *,
    batch_id: UUID,
    attempt_id: UUID,
    page_number: int,
    page_fingerprint: str,
    entries: tuple[ManifestEntry, ...],
    now: datetime | None = None,
) -> ManifestPageResult:
    now = now or timezone.now()
    _validate_manifest_page(page_number, page_fingerprint, entries)
    persisted_fingerprint = _manifest_page_fingerprint(page_fingerprint, entries)
    with _persist_callback_transition(attempt_id=attempt_id, clock=lambda: now):
        with transaction.atomic():
            batch, attempt = _locked_active_attempt(
                batch_id=batch_id,
                attempt_id=attempt_id,
                item_id=None,
                kind="manifest",
                now=now,
            )
            _require_batch_eligible(batch)
            existing = ImportManifestPage.objects.filter(
                batch=batch, page_number=page_number
            ).first()
            if existing is not None:
                if existing.fingerprint != persisted_fingerprint:
                    raise ImportConflict(
                        "manifest_changed", "The source changed during enumeration."
                    )
                return ManifestPageResult(page_number=page_number, replayed=True)

            counts = _entry_counts(entries)
            prior_count = sum(batch.manifest_pages.values_list("entry_count", flat=True))
            if prior_count + len(entries) > MAX_MANIFEST_ENTRIES:
                raise ImportConflict(
                    "manifest_too_large", "The source contains more than 10,000 entries."
                )
            ImportManifestPage.objects.create(
                batch=batch,
                page_number=page_number,
                fingerprint=persisted_fingerprint,
                entry_count=len(entries),
                jpeg_count=counts["jpeg"],
                directory_count=counts["directory"],
                unsupported_count=counts["unsupported"],
            )
            ImportItem.objects.bulk_create(
                [
                    ImportItem(
                        batch=batch,
                        source_path=entry.path,
                        filename=entry.name,
                        byte_size=entry.size,
                        source_sha256=entry.sha256,
                        source_md5=entry.md5,
                        source_version=entry.version,
                        status=(
                            ImportItem.Status.ERROR
                            if entry.size is not None and entry.size > MAX_IMPORT_BYTES
                            else ImportItem.Status.PENDING
                        ),
                        error_code=(
                            "file_too_large"
                            if entry.size is not None and entry.size > MAX_IMPORT_BYTES
                            else ""
                        ),
                        completed_at=(
                            now
                            if entry.size is not None and entry.size > MAX_IMPORT_BYTES
                            else None
                        ),
                    )
                    for entry in entries
                    if entry.kind == "jpeg"
                ]
            )
            batch.last_activity_at = now
            batch.save(update_fields=["last_activity_at"])
            return ManifestPageResult(page_number=page_number, replayed=False)


def _finish_manifest(
    *,
    batch_id: UUID,
    attempt_id: UUID,
    canonical_source_key: str,
    now: datetime | None = None,
) -> ImportResult:
    now = now or timezone.now()
    if not canonical_source_key or len(canonical_source_key) > 512:
        raise ImportConflict("invalid_source", "The canonical source key is invalid.")
    with transaction.atomic():
        completed_batch = (
            ImportBatch.objects.select_for_update(of=("self",))
            .select_related("scope")
            .get(pk=batch_id)
        )
        if completed_batch.manifest_complete:
            attempt = ImportAttempt.objects.get(pk=attempt_id, batch=completed_batch)
            if (
                attempt.kind == "manifest"
                and attempt.status == "succeeded"
                and completed_batch.scope is not None
                and completed_batch.scope.canonical_source_key == canonical_source_key
            ):
                return _result(completed_batch)
            raise ImportConflict("manifest_changed", "The manifest completion conflicts.")
        batch, attempt = _locked_active_attempt(
            batch_id=batch_id,
            attempt_id=attempt_id,
            item_id=None,
            kind="manifest",
            now=now,
        )
        _require_batch_eligible(batch)
        pages = list(batch.manifest_pages.order_by("page_number"))
        if not pages or [page.page_number for page in pages] != list(range(len(pages))):
            raise ImportConflict("manifest_incomplete", "Manifest pages are incomplete.")
        scope, _ = ImportScope.objects.get_or_create(
            owner_id=batch.owner_id,
            event_id=batch.event_id,
            folder_id=batch.folder_id,
            canonical_source_key=canonical_source_key,
        )
        batch.scope = scope
        batch.manifest_complete = True
        batch.jpeg_count = sum(page.jpeg_count for page in pages)
        batch.directory_count = sum(page.directory_count for page in pages)
        batch.unsupported_count = sum(page.unsupported_count for page in pages)
        batch.error_code = ""
        batch.last_activity_at = now
        batch.status = (
            ImportBatch.Status.TRANSFERRING if batch.jpeg_count else ImportBatch.Status.COMPLETED
        )
        batch.completed_at = None if batch.jpeg_count else now
        batch.save(
            update_fields=[
                "scope",
                "manifest_complete",
                "jpeg_count",
                "directory_count",
                "unsupported_count",
                "error_code",
                "last_activity_at",
                "status",
                "completed_at",
            ]
        )
        _finish_attempt(attempt, "succeeded", now=now)
        _derive_batch(batch, now=now)
        return _result(batch)


def finish_manifest(
    *,
    batch_id: UUID,
    attempt_id: UUID,
    canonical_source_key: str,
    now: datetime | None = None,
) -> ImportResult:
    observed_at = now or timezone.now()
    with _persist_callback_transition(attempt_id=attempt_id, clock=lambda: observed_at):
        return _finish_manifest(
            batch_id=batch_id,
            attempt_id=attempt_id,
            canonical_source_key=canonical_source_key,
            now=observed_at,
        )


def claim_import_work(*, lease_seconds: int = 120, now: datetime | None = None) -> ClaimedImport:
    if not 1 <= lease_seconds <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    now = now or timezone.now()
    with transaction.atomic():
        _expire_attempts(now)
        batch_ids = list(
            ImportBatch.objects.filter(
                status__in=(
                    ImportBatch.Status.QUEUED,
                    ImportBatch.Status.ENUMERATING,
                    ImportBatch.Status.TRANSFERRING,
                    ImportBatch.Status.PAUSED,
                )
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        for batch_id in batch_ids:
            batch = (
                ImportBatch.objects.select_for_update(skip_locked=True).filter(pk=batch_id).first()
            )
            if batch is None or _has_live_attempt(batch, now):
                continue
            if not _owner_is_eligible(batch.owner_id):
                if batch.status != ImportBatch.Status.PAUSED:
                    batch.status = ImportBatch.Status.PAUSED
                    batch.last_activity_at = now
                    batch.save(update_fields=["status", "last_activity_at"])
                continue
            if not batch.manifest_complete:
                if batch.manifest_attempts >= MAX_OPERATION_ATTEMPTS:
                    batch.status = ImportBatch.Status.FAILED
                    batch.completed_at = now
                    batch.save(update_fields=["status", "completed_at"])
                    continue
                batch.manifest_attempts += 1
                batch.status = ImportBatch.Status.ENUMERATING
                batch.last_activity_at = now
                batch.save(update_fields=["manifest_attempts", "status", "last_activity_at"])
                return _create_claim(
                    batch=batch,
                    item=None,
                    kind="manifest",
                    now=now,
                    lease_seconds=lease_seconds,
                )

            item = (
                ImportItem.objects.select_for_update(skip_locked=True)
                .filter(batch=batch, status=ImportItem.Status.PENDING)
                .order_by("source_path", "id")
                .first()
            )
            if item is None:
                _derive_batch(batch, now=now)
                continue
            if item.download_attempts >= MAX_OPERATION_ATTEMPTS:
                item.status = ImportItem.Status.ERROR
                item.error_code = "attempts_exhausted"
                item.completed_at = now
                item.save(update_fields=["status", "error_code", "completed_at"])
                _derive_batch(batch, now=now)
                continue
            item.download_attempts += 1
            item.status = ImportItem.Status.CLAIMED
            item.save(update_fields=["download_attempts", "status"])
            batch.status = ImportBatch.Status.TRANSFERRING
            batch.last_activity_at = now
            batch.save(update_fields=["status", "last_activity_at"])
            return _create_claim(
                batch=batch,
                item=item,
                kind="file",
                now=now,
                lease_seconds=lease_seconds,
            )
    return ClaimedImport(kind="empty")


def _renew_import_lease(
    attempt_id: UUID, *, lease_seconds: int = 120, now: datetime | None = None
) -> ClaimedImport:
    if not 1 <= lease_seconds <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    now = now or timezone.now()
    with transaction.atomic():
        attempt = (
            ImportAttempt.objects.select_for_update().select_related("batch").get(pk=attempt_id)
        )
        if attempt.status != ImportAttempt.Status.ACTIVE or attempt.lease_expires_at <= now:
            if attempt.status == ImportAttempt.Status.ACTIVE:
                _expire_attempt(attempt, now)
            raise ImportConflict("stale_attempt", "The import lease is no longer current.")
        _require_batch_eligible(attempt.batch)
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.save(update_fields=["heartbeat_at", "lease_expires_at"])
        return ClaimedImport(
            kind=attempt.kind,
            batch_id=attempt.batch_id,
            item_id=attempt.item_id,
            attempt_id=attempt.id,
            lease_expires_at=attempt.lease_expires_at,
        )


def renew_import_lease(
    attempt_id: UUID, *, lease_seconds: int = 120, now: datetime | None = None
) -> ClaimedImport:
    observed_at = now or timezone.now()
    with _persist_callback_transition(attempt_id=attempt_id, clock=lambda: observed_at):
        return _renew_import_lease(
            attempt_id,
            lease_seconds=lease_seconds,
            now=observed_at,
        )


def prepare_import_upload(
    *,
    attempt_id: UUID,
    item_id: UUID,
    content_sha256: str,
    byte_size: int,
    storage: ImportStorage,
    oriented_geometry: tuple[int, int] | None = None,
    now: datetime | None = None,
) -> PreparedImportUpload:
    now = now or timezone.now()
    if _SHA256.fullmatch(content_sha256) is None:
        raise ImportConflict("invalid_sha256", "The verified SHA-256 is invalid.")
    if oriented_geometry is not None and (
        len(oriented_geometry) != 2 or any(value < 1 for value in oriented_geometry)
    ):
        raise ImportConflict("invalid_geometry", "The verified JPEG geometry is invalid.")
    existing = ImportItem.objects.filter(pk=item_id).only("id", "status").first()
    if existing is not None and existing.status == ImportItem.Status.DUPLICATE:
        return PreparedImportUpload(status="duplicate", item_id=existing.id)
    with _persist_callback_transition(
        attempt_id=attempt_id,
        item_id=item_id,
        clock=lambda: now,
    ):
        with transaction.atomic():
            item = ImportItem.objects.select_for_update().get(pk=item_id)
            batch, attempt = _locked_active_attempt(
                batch_id=item.batch_id,
                attempt_id=attempt_id,
                item_id=item.id,
                kind="file",
                now=now,
            )
            _require_batch_eligible(batch)
            if item.status == ImportItem.Status.DUPLICATE:
                return PreparedImportUpload(status="duplicate", item_id=item.id)
            if item.status == ImportItem.Status.UPLOADING:
                if attempt.content_sha256 != content_sha256 or attempt.byte_size != byte_size:
                    raise ImportConflict(
                        "submission_conflict",
                        "The upload preparation conflicts with its checkpoint.",
                    )
                if not attempt.incoming_key:
                    raise ImportConflict(
                        "missing_checkpoint", "The incoming object key is missing."
                    )
                incoming_key = attempt.incoming_key
            else:
                if item.status != ImportItem.Status.CLAIMED:
                    raise ImportConflict("item_not_claimed", "The import item is not claimed.")
                if byte_size != item.byte_size or (
                    item.source_sha256 is not None and item.source_sha256 != content_sha256
                ):
                    raise ImportConflict(
                        "source_changed", "The source file changed after enumeration."
                    )
                if byte_size > MAX_IMPORT_BYTES:
                    raise ImportConflict(
                        "file_too_large", "The source file exceeds the import byte limit."
                    )
                if batch.scope_id is None:
                    raise ImportConflict(
                        "manifest_incomplete", "The canonical import scope is missing."
                    )
                ImportScope.objects.select_for_update().get(pk=batch.scope_id)
                duplicate = ImportedContent.objects.filter(
                    scope_id=batch.scope_id,
                    sha256=content_sha256,
                    byte_size=byte_size,
                ).first()
                if duplicate is not None:
                    item.content_sha256 = content_sha256
                    item.status = ImportItem.Status.DUPLICATE
                    item.photo = duplicate.photo
                    item.error_code = ""
                    item.completed_at = now
                    item.save(
                        update_fields=[
                            "content_sha256",
                            "status",
                            "photo",
                            "error_code",
                            "completed_at",
                        ]
                    )
                    _finish_attempt(attempt, "succeeded", now=now)
                    _derive_batch(batch, now=now)
                    return PreparedImportUpload(status="duplicate", item_id=item.id)
                if item.upload_attempts >= MAX_OPERATION_ATTEMPTS:
                    raise ImportConflict("attempts_exhausted", "Upload attempts are exhausted.")
                item.upload_attempts += 1
                item.status = ImportItem.Status.UPLOADING
                item.error_code = ""
                item.save(update_fields=["upload_attempts", "status", "error_code"])
                attempt.content_sha256 = content_sha256
                attempt.byte_size = byte_size
                attempt.incoming_key = f"incoming/{batch.id}/{attempt.id}"
                attempt.final_key = f"originals/{attempt.id.hex}"
                if oriented_geometry is not None:
                    attempt.oriented_width, attempt.oriented_height = oriented_geometry
                attempt.save(
                    update_fields=[
                        "content_sha256",
                        "byte_size",
                        "incoming_key",
                        "final_key",
                        "oriented_width",
                        "oriented_height",
                    ]
                )
                incoming_key = attempt.incoming_key
    assert incoming_key is not None
    grant = storage.create_presigned_post(incoming_key=incoming_key, max_bytes=byte_size)
    return PreparedImportUpload(status="upload", item_id=item.id, grant=grant)


def _record_import_failure(
    *,
    attempt_id: UUID,
    operation: Literal["manifest", "download", "upload", "publication"],
    code: str,
    retryable: bool,
    now: datetime | None = None,
) -> ImportResult:
    now = now or timezone.now()
    with transaction.atomic():
        attempt = ImportAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status != ImportAttempt.Status.ACTIVE or attempt.lease_expires_at <= now:
            if attempt.status == "failed" and attempt.error_code == code:
                return _result(ImportBatch.objects.get(pk=attempt.batch_id))
            raise ImportConflict("stale_attempt", "The import lease is no longer current.")
        batch = ImportBatch.objects.select_for_update().get(pk=attempt.batch_id)
        _require_batch_eligible(batch)
        if operation == "manifest":
            if attempt.kind != ImportAttempt.Kind.MANIFEST:
                raise ImportConflict("attempt_kind_mismatch", "This is not a manifest attempt.")
            batch.manifest_pages.all().delete()
            batch.items.all().delete()
            batch.jpeg_count = batch.directory_count = batch.unsupported_count = 0
            batch.error_code = code
            batch.status = (
                ImportBatch.Status.QUEUED
                if retryable and batch.manifest_attempts < MAX_OPERATION_ATTEMPTS
                else ImportBatch.Status.FAILED
            )
            batch.completed_at = None if batch.status == ImportBatch.Status.QUEUED else now
            batch.last_activity_at = now
            batch.save(
                update_fields=[
                    "jpeg_count",
                    "directory_count",
                    "unsupported_count",
                    "error_code",
                    "status",
                    "completed_at",
                    "last_activity_at",
                ]
            )
        else:
            if attempt.kind != ImportAttempt.Kind.FILE or attempt.item is None:
                raise ImportConflict("attempt_kind_mismatch", "This is not a file attempt.")
            item = ImportItem.objects.select_for_update().get(pk=attempt.item_id)
            operation_attempts = (
                item.upload_attempts
                if operation in {"upload", "publication"}
                else item.download_attempts
            )
            if retryable and operation_attempts < MAX_OPERATION_ATTEMPTS:
                item.status = ImportItem.Status.PENDING
                item.completed_at = None
            else:
                item.status = ImportItem.Status.ERROR
                item.completed_at = now
            item.error_code = code
            item.save(update_fields=["status", "completed_at", "error_code"])
            _derive_batch(batch, now=now)
        _finish_attempt(attempt, "failed", now=now, error_code=code)
        return _result(batch)


def record_import_failure(
    *,
    attempt_id: UUID,
    operation: Literal["manifest", "download", "upload", "publication"],
    code: str,
    retryable: bool,
    now: datetime | None = None,
) -> ImportResult:
    observed_at = now or timezone.now()
    with _persist_callback_transition(attempt_id=attempt_id, clock=lambda: observed_at):
        return _record_import_failure(
            attempt_id=attempt_id,
            operation=operation,
            code=code,
            retryable=retryable,
            now=observed_at,
        )


def retry_import_errors(
    *, actor: AbstractBaseUser, batch_id: UUID, now: datetime | None = None
) -> ImportResult:
    now = now or timezone.now()
    owner = _fresh_eligible_owner(actor.pk)
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch_id, owner=owner)
        if not batch.manifest_complete:
            if batch.status != ImportBatch.Status.FAILED:
                raise ImportConflict("nothing_to_retry", "The import has no manifest error.")
            batch.manifest_attempts = 0
            batch.error_code = ""
            batch.status = ImportBatch.Status.QUEUED
            batch.completed_at = None
        else:
            failed = list(batch.items.select_for_update().filter(status=ImportItem.Status.ERROR))
            if not failed:
                raise ImportConflict("nothing_to_retry", "The import has no failed items.")
            for item in failed:
                item.status = ImportItem.Status.PENDING
                item.download_attempts = 0
                item.upload_attempts = 0
                item.retry_generation += 1
                item.error_code = ""
                item.completed_at = None
                item.save(
                    update_fields=[
                        "status",
                        "download_attempts",
                        "upload_attempts",
                        "retry_generation",
                        "error_code",
                        "completed_at",
                    ]
                )
            batch.status = ImportBatch.Status.TRANSFERRING
            batch.completed_at = None
            batch.error_code = ""
        batch.last_activity_at = now
        batch.save(
            update_fields=[
                "manifest_attempts",
                "error_code",
                "status",
                "completed_at",
                "last_activity_at",
            ]
        )
        return _result(batch)


def _validate_manifest_page(
    page_number: int, page_fingerprint: str, entries: tuple[ManifestEntry, ...]
) -> None:
    if page_number < 0 or not page_fingerprint or len(page_fingerprint) > 64:
        raise ImportConflict("invalid_manifest_page", "The manifest page is invalid.")
    for entry in entries:
        if (
            entry.kind not in {"jpeg", "directory", "unsupported"}
            or not entry.path
            or not entry.name
        ):
            raise ImportConflict("invalid_manifest_entry", "A manifest entry is invalid.")
        if entry.kind == "jpeg" and (entry.size is None or entry.size < 1):
            raise ImportConflict("invalid_manifest_entry", "A JPEG size is invalid.")
        if entry.sha256 is not None and _SHA256.fullmatch(entry.sha256) is None:
            raise ImportConflict("invalid_manifest_entry", "A source SHA-256 is invalid.")
        if entry.md5 is not None and _MD5.fullmatch(entry.md5) is None:
            raise ImportConflict("invalid_manifest_entry", "A source MD5 is invalid.")


def _entry_counts(entries: tuple[ManifestEntry, ...]) -> dict[str, int]:
    return {
        kind: sum(entry.kind == kind for entry in entries)
        for kind in ("jpeg", "directory", "unsupported")
    }


def _manifest_page_fingerprint(source_fingerprint: str, entries: tuple[ManifestEntry, ...]) -> str:
    payload = {
        "source_fingerprint": source_fingerprint,
        "entries": [asdict(entry) for entry in entries],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _fresh_eligible_owner(owner_id: object) -> AbstractBaseUser:
    owner = get_user_model().objects.get(pk=owner_id)
    if not owner.is_active or not owner.has_perm("ingestion.upload_photos"):
        raise ImportConflict("permission_denied", "Photo upload permission is required.")
    if not is_enabled(YANDEX_DISK_IMPORT, owner):
        raise ImportConflict("feature_paused", "Yandex Disk import is paused.")
    return owner


def _owner_is_eligible(owner_id: object) -> bool:
    try:
        _fresh_eligible_owner(owner_id)
    except ImportConflict:
        return False
    return True


def _require_batch_eligible(batch: ImportBatch) -> None:
    _fresh_eligible_owner(batch.owner_id)


@contextmanager
def _persist_callback_transition(
    *,
    attempt_id: UUID,
    clock: Callable[[], datetime],
    item_id: UUID | None = None,
) -> Iterator[None]:
    """Persist callback state changes after its work transaction rolls back."""
    try:
        yield
    except ImportConflict as error:
        observed_at = clock()
        if error.code in {"feature_paused", "permission_denied"}:
            _persist_paused_batch(attempt_id=attempt_id, now=observed_at)
        elif error.code == "stale_attempt":
            _persist_expired_attempt(attempt_id=attempt_id, now=observed_at)
        elif item_id is not None and error.code in {
            "source_changed",
            "attempts_exhausted",
            "file_too_large",
        }:
            _persist_failed_item(
                attempt_id=attempt_id,
                item_id=item_id,
                code=error.code,
                now=observed_at,
            )
        raise


def _persist_paused_batch(*, attempt_id: UUID, now: datetime) -> None:
    attempt_ref = ImportAttempt.objects.only("batch_id").get(pk=attempt_id)
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=attempt_ref.batch_id)
        attempt = ImportAttempt.objects.select_for_update().get(pk=attempt_id, batch=batch)
        if attempt.status == ImportAttempt.Status.ACTIVE:
            batch.status = ImportBatch.Status.PAUSED
            batch.last_activity_at = now
            batch.save(update_fields=["status", "last_activity_at"])


def _persist_expired_attempt(*, attempt_id: UUID, now: datetime) -> None:
    attempt_ref = ImportAttempt.objects.only("batch_id").get(pk=attempt_id)
    with transaction.atomic():
        ImportBatch.objects.select_for_update().get(pk=attempt_ref.batch_id)
        attempt = ImportAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status == ImportAttempt.Status.ACTIVE and attempt.lease_expires_at <= now:
            _expire_attempt(attempt, now)


def _persist_failed_item(*, attempt_id: UUID, item_id: UUID, code: str, now: datetime) -> None:
    with transaction.atomic():
        item = ImportItem.objects.select_for_update().get(pk=item_id)
        batch = ImportBatch.objects.select_for_update().get(pk=item.batch_id)
        attempt = ImportAttempt.objects.select_for_update().get(
            pk=attempt_id,
            batch=batch,
            item=item,
        )
        if attempt.status == ImportAttempt.Status.ACTIVE:
            _fail_item(item, attempt, code=code, now=now)
            _derive_batch(batch, now=now)


def _locked_active_attempt(
    *,
    batch_id: UUID,
    attempt_id: UUID,
    item_id: UUID | None,
    kind: str,
    now: datetime | None = None,
    clock: Callable[[], datetime] = timezone.now,
) -> tuple[ImportBatch, ImportAttempt]:
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    attempt = ImportAttempt.objects.select_for_update().get(pk=attempt_id, batch=batch)
    observed_at = now if now is not None else clock()
    if (
        attempt.kind != kind
        or attempt.item_id != item_id
        or attempt.status != ImportAttempt.Status.ACTIVE
        or attempt.lease_expires_at <= observed_at
    ):
        raise ImportConflict("stale_attempt", "The import lease is no longer current.")
    return batch, attempt


def _has_live_attempt(batch: ImportBatch, now: datetime) -> bool:
    return batch.attempts.filter(
        status=ImportAttempt.Status.ACTIVE, lease_expires_at__gt=now
    ).exists()


def _expire_attempts(now: datetime) -> None:
    attempts = list(
        ImportAttempt.objects.select_for_update().filter(
            status=ImportAttempt.Status.ACTIVE, lease_expires_at__lte=now
        )
    )
    for attempt in attempts:
        _expire_attempt(attempt, now)


def _expire_attempt(attempt: ImportAttempt, now: datetime) -> None:
    if attempt.kind == "manifest":
        batch = ImportBatch.objects.select_for_update().get(pk=attempt.batch_id)
        batch.manifest_pages.all().delete()
        batch.items.all().delete()
        batch.jpeg_count = 0
        batch.directory_count = 0
        batch.unsupported_count = 0
        batch.save(update_fields=["jpeg_count", "directory_count", "unsupported_count"])
    elif attempt.item_id is not None:
        item = ImportItem.objects.select_for_update().get(pk=attempt.item_id)
        if item.status in {ImportItem.Status.CLAIMED, ImportItem.Status.UPLOADING}:
            item.status = ImportItem.Status.PENDING
            item.save(update_fields=["status"])
    _finish_attempt(attempt, "expired", now=now)


def _create_claim(
    *,
    batch: ImportBatch,
    item: ImportItem | None,
    kind: Literal["manifest", "file"],
    now: datetime,
    lease_seconds: int,
) -> ClaimedImport:
    expires_at = now + timedelta(seconds=lease_seconds)
    attempt = ImportAttempt.objects.create(
        batch=batch,
        item=item,
        kind=kind,
        claimed_at=now,
        heartbeat_at=now,
        lease_expires_at=expires_at,
    )
    return ClaimedImport(
        kind=kind,
        batch_id=batch.id,
        item_id=item.id if item is not None else None,
        attempt_id=attempt.id,
        lease_expires_at=expires_at,
    )


def _finish_attempt(
    attempt: ImportAttempt, status: str, *, now: datetime, error_code: str = ""
) -> None:
    attempt.status = status
    attempt.terminal_at = now
    attempt.error_code = error_code
    attempt.save(update_fields=["status", "terminal_at", "error_code"])


def _fail_item(item: ImportItem, attempt: ImportAttempt, *, code: str, now: datetime) -> None:
    item.status = ImportItem.Status.ERROR
    item.error_code = code
    item.completed_at = now
    item.save(update_fields=["status", "error_code", "completed_at"])
    _finish_attempt(attempt, "failed", now=now, error_code=code)


def _derive_batch(batch: ImportBatch, *, now: datetime) -> None:
    if not batch.manifest_complete:
        return
    statuses = list(batch.items.values_list("status", flat=True))
    if not statuses:
        batch.status = ImportBatch.Status.COMPLETED
    elif any(
        status
        in {ImportItem.Status.PENDING, ImportItem.Status.CLAIMED, ImportItem.Status.UPLOADING}
        for status in statuses
    ):
        batch.status = ImportBatch.Status.TRANSFERRING
    elif any(status == ImportItem.Status.ERROR for status in statuses):
        batch.status = ImportBatch.Status.PARTIAL
    else:
        batch.status = ImportBatch.Status.COMPLETED
    batch.completed_at = (
        now if batch.status in {ImportBatch.Status.COMPLETED, ImportBatch.Status.PARTIAL} else None
    )
    batch.last_activity_at = now
    batch.save(update_fields=["status", "completed_at", "last_activity_at"])


def _result(batch: ImportBatch) -> ImportResult:
    item_counts = {
        status: batch.items.filter(status=status).count()
        for status in (
            ImportItem.Status.IMPORTED,
            ImportItem.Status.DUPLICATE,
            ImportItem.Status.ERROR,
        )
    }
    return ImportResult(
        id=batch.id,
        status=batch.status,
        jpeg_count=batch.jpeg_count,
        directory_count=batch.directory_count,
        unsupported_count=batch.unsupported_count,
        imported_count=item_counts[ImportItem.Status.IMPORTED],
        duplicate_count=item_counts[ImportItem.Status.DUPLICATE],
        error_count=item_counts[ImportItem.Status.ERROR],
        manifest_attempts=batch.manifest_attempts,
    )
