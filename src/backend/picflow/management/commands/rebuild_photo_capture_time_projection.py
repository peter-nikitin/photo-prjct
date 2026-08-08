import json

from django.core.management.base import BaseCommand, CommandError
from picflow.capture_time_projection import rebuild_events
from picflow.models import Event


class Command(BaseCommand):
    help = "Dry-run or rebuild the privacy-safe Photo capture-time projection."

    def add_arguments(self, parser) -> None:
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument("--event-id", type=int)
        scope.add_argument("--all-events", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options) -> None:
        event_id = options["event_id"]
        try:
            totals = rebuild_events(event_id=event_id, apply=options["apply"])
        except Event.DoesNotExist as error:
            raise CommandError("event does not exist") from error
        report = {
            "action": "applied" if options["apply"] else "dry_run",
            **totals,
            "scope": "all_events" if options["all_events"] else "event",
        }
        if not options["apply"]:
            report["would_change"] = report.pop("changed")
        self.stdout.write(
            json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        if totals["exhausted"]:
            raise CommandError("projection rebuild did not converge")
