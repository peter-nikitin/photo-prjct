"""Fence old preview publications before the gallery projection rebuild."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DatabaseError
from picflow.gallery_media_projection import drain_gallery_media_publications


class Command(BaseCommand):
    help = "Fence all in-progress gallery-media publications before projection rebuild."

    def add_arguments(self, parser: ArgumentParser | CommandParser) -> None:
        parser.add_argument("--all-events", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["all_events"]:
            raise CommandError("--all-events is required")
        try:
            report = drain_gallery_media_publications()
        except DatabaseError:
            raise CommandError("gallery-media publication drain failed") from None
        self.stdout.write(
            json.dumps(
                {
                    "fenced_attempt_count": report.fenced_attempt_count,
                    "scope": "all_events",
                    "status": "ok",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
