import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError
from picflow.gallery_media_projection import verify_gallery_media_projection


class Command(BaseCommand):
    help = "Verify the gallery-media projection with an aggregate symmetric difference."

    def add_arguments(self, parser) -> None:
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument("--all-events", action="store_true")
        parser.add_argument("--require-clean", action="store_true")

    def handle(self, *args, **options) -> None:
        report = verify_gallery_media_projection()
        payload = {**asdict(report), "scope": "all_events"}
        self.stdout.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        if options["require_clean"] and not report.clean:
            raise CommandError("projection reconciliation is not clean")
