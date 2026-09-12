"""Bounded, opt-in deletion of inactive attempt-owned objects, never import history."""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from picflow.models import Photo

from ingestion.models import ImportAttempt, ImportBatch, ImportItem
from ingestion.storage import PrivateUploadStorage, StorageError


class Command(BaseCommand):
    help = (
        "Inspect stale import objects; --apply deletes only unreferenced expired attempt objects."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 1000:
            raise CommandError("--limit must be between 1 and 1000")
        cutoff = timezone.now() - timedelta(hours=24)
        candidates = list(
            ImportAttempt.objects.filter(
                heartbeat_at__lt=cutoff,
                lease_expires_at__lte=timezone.now(),
                batch__last_activity_at__lt=cutoff,
            )
            .alias(
                incoming_referenced=Exists(
                    Photo.objects.filter(original_key=OuterRef("incoming_key"))
                ),
                final_referenced=Exists(Photo.objects.filter(original_key=OuterRef("final_key"))),
            )
            .filter(
                Q(incoming_key__isnull=False, incoming_referenced=False)
                | Q(final_key__isnull=False, final_referenced=False)
            )
            .order_by("heartbeat_at")
            .values_list("pk", "item_id", "batch_id")[:limit]
        )
        storage = PrivateUploadStorage() if options["apply"] else None
        eligible = cleaned = failed = 0
        for attempt_id, item_id, batch_id in candidates:
            # Same item -> batch -> attempt order as authoritative publication. Keeping
            # these locks across bounded S3 deletes fences final publication and renewal.
            with transaction.atomic():
                item = (
                    ImportItem.objects.select_for_update(skip_locked=True)
                    .filter(pk=item_id)
                    .first()
                )
                if item is None:
                    continue
                batch = (
                    ImportBatch.objects.select_for_update(skip_locked=True)
                    .filter(pk=batch_id)
                    .first()
                )
                if batch is None:
                    continue
                attempt = (
                    ImportAttempt.objects.select_for_update(skip_locked=True)
                    .filter(pk=attempt_id)
                    .first()
                )
                now = timezone.now()
                if (
                    attempt is None
                    or attempt.heartbeat_at >= cutoff
                    or attempt.lease_expires_at > now
                    or batch.last_activity_at >= cutoff
                    or batch.attempts.filter(status="active", lease_expires_at__gt=now).exists()
                ):
                    continue
                keys = [
                    key
                    for key in (attempt.incoming_key, attempt.final_key)
                    if key and not Photo.objects.filter(original_key=key).exists()
                ]
                # A duplicate links to the winner Photo, but its distinct final may
                # still need deletion after interrupted best-effort publication cleanup.
                # Actual original-key references above protect winners authoritatively.
                if not keys:
                    continue
                eligible += 1
                if storage is None:
                    continue
                # Persist the fence before any deletion, including partial S3 failure.
                if attempt.status == ImportAttempt.Status.ACTIVE:
                    attempt.status = ImportAttempt.Status.EXPIRED
                    attempt.terminal_at = now
                    attempt.error_code = "cleanup_expired"
                    if item.status in {ImportItem.Status.CLAIMED, ImportItem.Status.UPLOADING}:
                        item.status = ImportItem.Status.PENDING
                        item.save(update_fields=["status"])
                attempt.save()
                try:
                    for key in keys:
                        storage.delete(key=key)
                except StorageError:
                    failed += 1
                else:
                    if attempt.incoming_key in keys:
                        attempt.incoming_key = None
                        attempt.verified_source_etag = ""
                    if attempt.final_key in keys:
                        attempt.final_key = None
                        attempt.verified_final_etag = ""
                    cleaned += 1
                attempt.save()
        self.stdout.write(
            f"mode={'apply' if options['apply'] else 'dry-run'} "
            f"inspected={len(candidates)} eligible={eligible} cleaned={cleaned} failed={failed}"
        )
        if failed:
            raise CommandError(
                "Import cleanup storage operation failed; retry the bounded command."
            )
