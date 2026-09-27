"""Private aggregate completeness report for the accepted current face cohort."""

import json

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.vector_reconciliation import verify_embeddings


class Command(BaseCommand):
    help = "Verify current accepted face vectors; gaps return a nonzero exit status."

    def add_arguments(self, parser):
        parser.add_argument("--event-slug")

    def handle(self, *args, **options):
        event = None
        if options["event_slug"] is not None:
            try:
                event = Event.objects.get(slug=options["event_slug"])
            except Event.DoesNotExist as exc:
                raise CommandError("Event does not exist") from exc
        report = verify_embeddings(event)
        self.stdout.write(json.dumps(report, sort_keys=True))
        if any(
            row[field] for row in report["groups"] for field in ("missing", "divergent", "invalid")
        ):
            raise CommandError("Eligible vector cohort is incomplete or invalid")
