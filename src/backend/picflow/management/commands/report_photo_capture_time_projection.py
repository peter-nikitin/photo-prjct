import json

from django.core.management.base import BaseCommand, CommandError
from picflow.capture_time_projection import report_events
from picflow.models import Event


class Command(BaseCommand):
    help = "Report privacy-safe Photo capture-time projection reconciliation aggregates."

    def add_arguments(self, parser) -> None:
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument("--event-id", type=int)
        scope.add_argument("--all-events", action="store_true")
        parser.add_argument("--require-clean", action="store_true")

    def handle(self, *args, **options) -> None:
        event_id = options["event_id"]
        all_events = bool(options["all_events"] or event_id is None)
        if event_id is not None and options["all_events"]:
            raise CommandError("--event-id is not allowed with --all-events")
        try:
            report = report_events(event_id=event_id)
        except Event.DoesNotExist as error:
            raise CommandError("event does not exist") from error
        report["scope"] = "all_events" if all_events else "event"
        self.stdout.write(
            json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        if options["require_clean"] and not report["clean"]:
            raise CommandError("projection reconciliation is not clean")
