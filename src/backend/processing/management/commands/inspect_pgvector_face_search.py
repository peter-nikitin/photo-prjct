"""Schema/state inventory safe before the vector expansion migration."""

import json

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.vector_reconciliation import inspect_inventory


class Command(BaseCommand):
    help = "Privately inventory schema readiness and bounded aggregate release state."

    def add_arguments(self, parser):
        parser.add_argument("--event-slug")

    def handle(self, *args, **options):
        event = None
        if options["event_slug"] is not None:
            try:
                event = Event.objects.get(slug=options["event_slug"])
            except Event.DoesNotExist as exc:
                raise CommandError("Event does not exist") from exc
        self.stdout.write(json.dumps(inspect_inventory(event), sort_keys=True))
