import json

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.historical_adaface import (
    activate_historical_adaface,
    enroll_historical_adaface,
    historical_adaface_status,
)


class Command(BaseCommand):
    help = "Inspect, enroll a bounded historical AdaFace batch, or activate one reconciled event."

    def add_arguments(self, parser) -> None:
        events = parser.add_mutually_exclusive_group(required=True)
        events.add_argument("--event-slug")
        events.add_argument("--event-id", type=int)
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument("--apply", action="store_true")
        actions.add_argument("--activate", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--cohort-sha256", default="")
        parser.add_argument("--quality-review-sha256", default="")
        parser.add_argument("--confirm-reviewed", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            event = (
                Event.objects.get(pk=options["event_id"])
                if options["event_id"]
                else Event.objects.get(slug=options["event_slug"])
            )
        except Event.DoesNotExist:
            raise CommandError("event does not exist") from None
        try:
            if options["apply"]:
                report = enroll_historical_adaface(
                    event, cohort_sha256=options["cohort_sha256"], limit=options["limit"]
                )
                mode = "apply"
            elif options["activate"]:
                report = activate_historical_adaface(
                    event,
                    cohort_sha256=options["cohort_sha256"],
                    quality_review_sha256=options["quality_review_sha256"],
                    review_confirmed=options["confirm_reviewed"],
                )
                mode = "activate"
            else:
                report = historical_adaface_status(event)
                mode = "dry_run"
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            json.dumps({**report, "mode": mode}, sort_keys=True, separators=(",", ":"))
        )
