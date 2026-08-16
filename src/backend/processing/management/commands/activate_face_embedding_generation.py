from __future__ import annotations

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.face_quality import (
    activate_face_embedding_generation,
    baseline_face_embedding_generations,
    candidate_face_embedding_generations,
    local_adaface_face_embedding_generations,
)


class Command(BaseCommand):
    help = "Append one reviewed event face-embedding generation selection."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.conflict_handler = "resolve"
        parser._optionals.conflict_handler = "resolve"
        parser.add_argument("event", nargs="?", help="Event primary key or slug")
        parser.add_argument("--event", dest="event_option", help="Event primary key or slug")
        parser.add_argument(
            "--generation", choices=("baseline", "candidate", "local-adaface"), required=True
        )
        parser.add_argument("--configuration-hash", default="")
        parser.add_argument("--evaluation-report-hash", default="")
        parser.add_argument("--manifest-sha256", default="")
        parser.add_argument("--confirm-reviewed", action="store_true")

    def handle(self, *args: object, **options: object) -> str:
        event_reference = options.get("event_option") or options.get("event")
        if isinstance(event_reference, int) and not isinstance(event_reference, bool):
            event_reference = str(event_reference)
        if not isinstance(event_reference, str) or not event_reference:
            raise CommandError("event is required")
        if options.get("confirm_reviewed") is not True:
            raise CommandError("review confirmation is required")
        event = self._event(event_reference)
        generation = options.get("generation")
        if generation == "baseline":
            generations = baseline_face_embedding_generations()
        elif generation == "candidate":
            generations = candidate_face_embedding_generations()
        else:
            manifest_sha256 = options.get("manifest_sha256")
            if not isinstance(manifest_sha256, str) or not manifest_sha256:
                raise CommandError("--manifest-sha256 is required for local AdaFace")
            if options.get("evaluation_report_hash"):
                raise CommandError(
                    "local AdaFace uses --manifest-sha256, not --evaluation-report-hash"
                )
            generations = local_adaface_face_embedding_generations()
            options["evaluation_report_hash"] = manifest_sha256
        try:
            activation = activate_face_embedding_generation(
                event=event,
                generations=generations,
                approved_configuration_hash=str(options.get("configuration_hash") or ""),
                evaluation_report_hash=str(options.get("evaluation_report_hash") or ""),
                review_confirmed=True,
            )
        except ValueError as error:
            raise CommandError("face-embedding generation activation failed") from error
        return str(activation.pk)

    @staticmethod
    def _event(reference: str) -> Event:
        try:
            return Event.objects.get(pk=int(reference))
        except (Event.DoesNotExist, ValueError):
            try:
                return Event.objects.get(slug=reference)
            except Event.DoesNotExist:
                raise CommandError("event not found") from None
