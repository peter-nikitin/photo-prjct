"""Physical retirement is explicitly reviewed and post-deployment only."""

import json

from django.core.management.base import BaseCommand, CommandError

from processing.services.legacy_embedding_retirement import (
    retire_physical_schema,
    verify_all_retained_events,
)


class Command(BaseCommand):
    help = "Bounded all-event native release gate; --execute retires obsolete physical schema."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--reviewed-release", action="store_true")
        parser.add_argument("--old-processes-drained", action="store_true")
        parser.add_argument("--active-build")
        parser.add_argument("--timeout-seconds", type=int, default=15)

    def handle(self, *args, **options):
        if options["execute"]:
            if not options["reviewed_release"] or not options["old_processes_drained"]:
                raise CommandError(
                    "Physical retirement requires reviewed release and old-process drain"
                )
            if not options["active_build"]:
                raise CommandError("Physical retirement requires active candidate build")
            report = retire_physical_schema(
                active_build=options["active_build"], timeout_seconds=options["timeout_seconds"]
            )
        else:
            report = verify_all_retained_events(timeout_seconds=options["timeout_seconds"])
        self.stdout.write(json.dumps(report, sort_keys=True))
