"""Sanitized, read-only import state inventory."""

import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from ingestion.models import ImportBatch


class Command(BaseCommand):
    help = "Read bounded import IDs, states and aggregate counts without source identifiers."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--batch", type=UUID)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("--limit must be between 1 and 1000")
        batches = ImportBatch.objects.order_by("-created_at")
        if options["batch"]:
            batches = batches.filter(pk=options["batch"])
        for batch in batches[: options["limit"]]:
            self.stdout.write(
                json.dumps(
                    dict(
                        batch_id=str(batch.pk),
                        event_id=batch.event_id,
                        folder_id=batch.folder_id,
                        owner_id=batch.owner_id,
                        status=batch.status,
                        manifest_complete=batch.manifest_complete,
                        items=list(
                            batch.items.values("status")
                            .annotate(count=Count("id"))
                            .order_by("status")
                        ),
                        attempts=list(
                            batch.attempts.values("status")
                            .annotate(count=Count("id"))
                            .order_by("status")
                        ),
                    ),
                    sort_keys=True,
                )
            )
