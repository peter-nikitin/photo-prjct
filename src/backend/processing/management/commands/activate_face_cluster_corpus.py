from __future__ import annotations

import re
from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.models import FaceClusterCorpus
from processing.services import face_cluster_corpora

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Command(BaseCommand):
    help = "Explicitly activate one published event face-cluster corpus."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.conflict_handler = "resolve"
        parser._optionals.conflict_handler = "resolve"
        parser.add_argument("event", nargs="?", help="Event primary key or slug")
        parser.add_argument("--event", dest="event_option", help="Event primary key or slug")
        parser.add_argument("--corpus", required=True, help="Published face-cluster corpus UUID")
        parser.add_argument("--policy-hash", dest="configuration_hash", required=True)
        parser.add_argument("--anchor-threshold", type=float, required=True)
        parser.add_argument("--evaluation-report-hash", required=True)
        parser.add_argument("--confirm-numeric-gates-reviewed", action="store_true")

    def handle(self, *args: object, **options: object) -> str:
        event_reference = options.get("event_option") or options.get("event")
        if isinstance(event_reference, int) and not isinstance(event_reference, bool):
            event_reference = str(event_reference)
        if not isinstance(event_reference, str) or not event_reference:
            raise CommandError("event is required")
        event = self._event(event_reference)
        corpus_reference = options.get("corpus")
        configuration_hash = options.get("configuration_hash")
        evaluation_report_hash = options.get("evaluation_report_hash")
        if (
            not isinstance(corpus_reference, str)
            or not _SHA256.fullmatch(
                configuration_hash if isinstance(configuration_hash, str) else ""
            )
            or not _SHA256.fullmatch(
                evaluation_report_hash if isinstance(evaluation_report_hash, str) else ""
            )
            or options.get("confirm_numeric_gates_reviewed") is not True
        ):
            raise CommandError("invalid corpus activation")
        assert isinstance(configuration_hash, str)
        assert isinstance(evaluation_report_hash, str)
        try:
            corpus = FaceClusterCorpus.objects.get(pk=corpus_reference)
            activation = face_cluster_corpora.activate_face_cluster_corpus(
                event=event,
                corpus=corpus,
                configuration_hash=configuration_hash,
                anchor_threshold=self._anchor_threshold(options),
                evaluation_report_hash=evaluation_report_hash,
                numeric_gates_reviewed=True,
            )
        except (FaceClusterCorpus.DoesNotExist, ValueError, TypeError):
            raise CommandError("face-cluster corpus activation failed") from None
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

    @staticmethod
    def _anchor_threshold(options: dict[str, object]) -> float:
        value = options.get("anchor_threshold")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandError("invalid corpus activation")
        return float(value)
