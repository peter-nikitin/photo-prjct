import signal
from threading import Event
from time import monotonic
from types import FrameType

from django.core.checks import Tags
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from picflow.archive_jobs import (
    process_next_archive,
    prune_archive_jobs,
    recover_interrupted_archives,
)

_WORKER_ADVISORY_LOCK = 806_220_02
_POLL_SECONDS = 2
_PRUNE_INTERVAL_SECONDS = 900


class Command(BaseCommand):
    help = "Prepare and publish page-scoped ZIP archives."
    requires_system_checks = [Tags.models]

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_WORKER_ADVISORY_LOCK])
            if not cursor.fetchone()[0]:
                raise CommandError("An archive worker is already running.")

        stopping = Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            stopping.set()

        previous_handler = signal.signal(signal.SIGTERM, request_stop)
        try:
            recover_interrupted_archives()
            next_prune = 0.0
            while not stopping.is_set():
                if monotonic() >= next_prune:
                    prune_archive_jobs()
                    next_prune = monotonic() + _PRUNE_INTERVAL_SECONDS
                did_work = process_next_archive()
                if options["once"]:
                    return
                if not did_work:
                    stopping.wait(_POLL_SECONDS)
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_WORKER_ADVISORY_LOCK])
