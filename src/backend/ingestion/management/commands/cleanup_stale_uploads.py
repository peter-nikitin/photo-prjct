from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.models import UploadBatch, UploadItem
from ingestion.storage import PrivateUploadStorage, StorageError


class Command(BaseCommand):
    help = "Delete objects belonging to upload batches inactive for at least 24 hours."

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        limit = options["limit"]
        if isinstance(limit, bool) or limit < 1 or limit > 1_000:
            raise CommandError("--limit must be between 1 and 1000")

        cutoff = timezone.now() - timedelta(seconds=settings.PHOTO_UPLOAD_STALE_AFTER_SECONDS)
        candidates = list(
            UploadBatch.objects.filter(
                status__in=(UploadBatch.Status.CREATED, UploadBatch.Status.UPLOADING),
                last_activity_at__lt=cutoff,
            )
            .order_by("last_activity_at")
            .values_list("pk", flat=True)[:limit]
        )
        storage = PrivateUploadStorage()
        cleaned = 0

        for batch_id in candidates:
            if self._cleanup_batch(batch_id=batch_id, cutoff=cutoff, storage=storage):
                cleaned += 1

        self.stdout.write(f"Cleaned {cleaned} stale upload batch(es).")

    def _cleanup_batch(self, *, batch_id, cutoff, storage: PrivateUploadStorage) -> bool:  # noqa: ANN001
        with transaction.atomic():
            batch = (
                UploadBatch.objects.select_for_update(skip_locked=True).filter(pk=batch_id).first()
            )
            if (
                batch is None
                or batch.status not in (UploadBatch.Status.CREATED, UploadBatch.Status.UPLOADING)
                or batch.last_activity_at >= cutoff
            ):
                return False

            items = list(batch.items.select_for_update().order_by("pk"))
            cleanup_failed = False
            for item in items:
                keys = []
                if item.status != UploadItem.Status.UPLOADED:
                    keys.append(item.incoming_key)
                if item.photo_id is None:
                    keys.append(item.final_key)

                try:
                    for key in keys:
                        storage.delete(key=key)
                except StorageError:
                    cleanup_failed = True
                    if item.status in (UploadItem.Status.PENDING, UploadItem.Status.AUTHORIZED):
                        item.error_code = "cleanup_storage_error"
                        item.sanitized_error_message = "Temporary storage cleanup failure."
                        item.save(update_fields=["error_code", "sanitized_error_message"])

            if cleanup_failed:
                return False

            completed_at = timezone.now()
            for item in items:
                if item.status in (UploadItem.Status.PENDING, UploadItem.Status.AUTHORIZED):
                    item.status = UploadItem.Status.FAILED
                    item.error_code = "upload_expired"
                    item.sanitized_error_message = "Upload expired after 24 hours of inactivity."
                    item.authorization_expires_at = None
                    item.completed_at = completed_at
                    item.save(
                        update_fields=[
                            "status",
                            "error_code",
                            "sanitized_error_message",
                            "authorization_expires_at",
                            "completed_at",
                        ]
                    )

            uploaded_count = sum(item.status == UploadItem.Status.UPLOADED for item in items)
            batch.status = (
                UploadBatch.Status.PARTIAL if uploaded_count else UploadBatch.Status.FAILED
            )
            batch.completed_at = completed_at
            batch.save(update_fields=["status", "completed_at"])
            return True
