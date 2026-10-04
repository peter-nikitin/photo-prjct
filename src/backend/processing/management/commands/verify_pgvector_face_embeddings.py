"""Private aggregate completeness report for the accepted current face cohort."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from picflow.models import Event

from processing.services.vector_reconciliation import verify_embeddings, verify_scalar_embeddings


class Command(BaseCommand):
    help = "Verify current accepted face vectors; gaps return a nonzero exit status."

    def add_arguments(self, parser):
        parser.add_argument("--event-slug")
        parser.add_argument("--scalar", action="store_true", help="Event-scoped SQL release check")
        parser.add_argument("--timeout-seconds", type=int, default=15)

    def handle(self, *args, **options):
        event = None
        if options["event_slug"] is not None:
            try:
                event = Event.objects.get(slug=options["event_slug"])
            except Event.DoesNotExist as exc:
                raise CommandError("Event does not exist") from exc
        if options["scalar"]:
            if event is None:
                raise CommandError("--scalar requires --event-slug")
            if not 1 <= options["timeout_seconds"] <= 60:
                raise CommandError("timeout must be 1–60 seconds")
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        [str(options["timeout_seconds"] * 1000)],
                    )
                report = verify_scalar_embeddings(event)
        else:
            report = verify_embeddings(event)
        self.stdout.write(json.dumps(report, sort_keys=True))
        if any(
            row[field] for row in report["groups"] for field in ("missing", "divergent", "invalid")
        ):
            raise CommandError("Eligible vector cohort is incomplete or invalid")
