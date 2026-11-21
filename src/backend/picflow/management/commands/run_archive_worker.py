import signal
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from types import FrameType

from django.core.checks import Tags
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, connections
from picflow.archive_jobs import (
    process_next_archive,
    prune_archive_jobs,
    recover_interrupted_archives,
)

_WORKER_ADVISORY_LOCK = 806_220_02
_POLL_SECONDS = 2
_PRUNE_INTERVAL_SECONDS = 900


def _run_slot(stopping: Event) -> None:
    try:
        while not stopping.is_set():
            if not process_next_archive():
                stopping.wait(_POLL_SECONDS)
    finally:
        connections.close_all()


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
            prune_archive_jobs()
            next_prune = monotonic() + _PRUNE_INTERVAL_SECONDS
            if options["once"]:
                process_next_archive()
                return
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="archive") as pool:
                try:
                    slots = [pool.submit(_run_slot, stopping) for _ in range(2)]
                    while not stopping.is_set():
                        for slot in slots:
                            if slot.done():
                                slot.result()
                        if monotonic() >= next_prune:
                            prune_archive_jobs()
                            next_prune = monotonic() + _PRUNE_INTERVAL_SECONDS
                        stopping.wait(_POLL_SECONDS)
                finally:
                    stopping.set()
                for slot in slots:
                    slot.result()
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_WORKER_ADVISORY_LOCK])
