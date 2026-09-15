import json
from dataclasses import asdict

from django.core.management.base import BaseCommand
from picflow.gallery_media_projection import rebuild_gallery_media_projection


class Command(BaseCommand):
    help = "Dry-run or rebuild the gallery-media projection from accepted evidence."

    def add_arguments(self, parser) -> None:
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument("--all-events", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options) -> None:
        report = rebuild_gallery_media_projection(apply=options["apply"])
        payload = {
            "action": "applied" if options["apply"] else "dry_run",
            **asdict(report),
            "scope": "all_events",
        }
        self.stdout.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
