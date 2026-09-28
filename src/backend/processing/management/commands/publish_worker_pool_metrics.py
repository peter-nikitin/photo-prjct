import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from processing.services.worker_pool_metrics import observe_metrics, publish_metrics


class Command(BaseCommand):
    help = (
        "Observe worker demand (default); --publish explicitly writes complete Monitoring gauges."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--zone", required=True)
        parser.add_argument("--folder-id")
        parser.add_argument("--publish", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            if options["publish"]:
                if not options["folder_id"]:
                    raise ValueError("folder required")
                result = publish_metrics(options["zone"], options["folder_id"])
            else:
                result = observe_metrics(options["zone"])
        except (OSError, ValueError, RuntimeError, DatabaseError):
            raise CommandError("worker pool publication failed") from None
        self.stdout.write(json.dumps(result, separators=(",", ":"), sort_keys=True))
