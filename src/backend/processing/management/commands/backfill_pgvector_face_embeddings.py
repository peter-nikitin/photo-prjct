"""Explicit, bounded historical embedding population without inference."""

import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.vector_reconciliation import backfill_embeddings


class Command(BaseCommand):
    help = "Privately report bounded vector backfill; dry run unless --apply is supplied."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--max-rows", type=int, default=5000)
        parser.add_argument("--after", type=UUID)
        parser.add_argument("--event-slug")

    def handle(self, *args, **options):
        event = None
        if options["event_slug"] is not None:
            try:
                event = Event.objects.get(slug=options["event_slug"])
            except Event.DoesNotExist as exc:
                raise CommandError("Event does not exist") from exc
        try:
            report = backfill_embeddings(
                apply=options["apply"],
                batch_size=options["batch_size"],
                max_rows=options["max_rows"],
                after=options["after"],
                event=event,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, sort_keys=True))
        if report["invalid"] or report["divergent"]:
            raise CommandError(
                "Historical evidence requires reconciliation; no existing rows overwritten"
            )
