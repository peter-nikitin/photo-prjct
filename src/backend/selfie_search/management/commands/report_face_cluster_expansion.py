"""Emit one bounded aggregate-only face-cluster expansion report."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from selfie_search.services.cluster_reporting import build_cluster_expansion_report

_DATE_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


class Command(BaseCommand):
    help = "Report aggregate face-cluster expansion and labelled feedback for a Moscow date range."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--start", required=True)
        parser.add_argument("--end", required=True)
        parser.add_argument("--event")

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        start = _parse_date(options.get("start"), name="--start")
        end = _parse_date(options.get("end"), name="--end")
        raw_event = options.get("event")
        event = _parse_event(raw_event) if raw_event is not None else None
        try:
            report = build_cluster_expansion_report(start=start, end=end, event=event)
        except ValueError as exc:
            message = str(exc)
            if message == "start must be before end":
                raise CommandError("Invalid report bounds: --start must be before --end") from None
            raise CommandError("Invalid report arguments") from None
        self.stdout.write(
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        raise CommandError(f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f"{name} must be YYYY-MM-DD") from None


def _parse_event(value: object) -> int:
    if not isinstance(value, str):
        raise CommandError("--event must be a UUID")
    try:
        return int(value)
    except ValueError:
        raise CommandError("--event must be a UUID") from None
