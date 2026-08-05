from __future__ import annotations

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services import face_cluster_corpora


class Command(BaseCommand):
    help = "Build and publish one immutable event-scoped face-cluster corpus."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.conflict_handler = "resolve"
        parser._optionals.conflict_handler = "resolve"
        parser.add_argument("event", nargs="?", help="Event primary key or slug")
        parser.add_argument("--event", dest="event_option", help="Event primary key or slug")
        parser.add_argument("--version", type=int, required=True)
        parser.add_argument("--edge-threshold", type=float, required=True)
        parser.add_argument("--representative-threshold", type=float, required=True)
        parser.add_argument(
            "--distance-block-size",
            "--block-size",
            dest="distance_block_size",
            type=int,
            required=True,
        )
        parser.add_argument(
            "--max-candidate-edges",
            "--edge-limit",
            dest="max_candidate_edges",
            type=int,
            required=True,
        )
        parser.add_argument("--dimensions", type=int, default=None)

    def handle(self, *args: object, **options: object) -> str:
        event_reference = options.get("event_option") or options.get("event")
        if isinstance(event_reference, int) and not isinstance(event_reference, bool):
            event_reference = str(event_reference)
        if not isinstance(event_reference, str) or not event_reference:
            raise CommandError("event is required")
        event = self._event(event_reference)
        try:
            corpus = face_cluster_corpora.build_face_cluster_corpus(
                event=event,
                version=self._int_option(options, "version"),
                edge_threshold=self._float_option(options, "edge_threshold"),
                representative_threshold=self._float_option(options, "representative_threshold"),
                distance_block_size=self._int_option(options, "distance_block_size"),
                max_candidate_edges=self._int_option(options, "max_candidate_edges"),
                dimensions=self._optional_int_option(options, "dimensions"),
            )
        except Exception as exc:
            # Do not echo exception text: it may contain a photo, face, vector, or object identity.
            raise CommandError("face-cluster corpus build failed") from exc
        return str(corpus.pk)

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
    def _int_option(options: dict[str, object], name: str) -> int:
        value = options.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise CommandError(f"invalid {name}")
        return value

    @staticmethod
    def _float_option(options: dict[str, object], name: str) -> float:
        value = options.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandError(f"invalid {name}")
        return float(value)

    @staticmethod
    def _optional_int_option(options: dict[str, object], name: str) -> int | None:
        value = options.get(name)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise CommandError(f"invalid {name}")
        return value
