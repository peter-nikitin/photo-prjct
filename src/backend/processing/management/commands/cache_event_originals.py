from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from processing.services.event_original_cache import (
    CacheError,
    EventOriginalCache,
    S3EventOriginalStorage,
    select_event,
)


class Command(BaseCommand):
    help = "Materialize one verified private event-original cache for local evaluation."

    def add_arguments(self, parser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--event")
        source.add_argument("--latest-published", action="store_true")
        parser.add_argument("--output-root", type=Path)

    def handle(self, *args, **options) -> None:
        try:
            event = select_event(
                event_slug=options["event"], latest_published=options["latest_published"]
            )
            output_root = options["output_root"]
            if output_root is None:
                output_root = Path.home() / "Documents/Projects/photo-prjct-private/event-corpora"
            result = EventOriginalCache(storage=S3EventOriginalStorage()).cache(
                event=event, output_root=output_root
            )
        except CacheError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            f"Cache complete: {result.downloaded_count + result.reused_count} originals "
            f"({result.downloaded_count} downloaded, {result.reused_count} reused)."
        )
