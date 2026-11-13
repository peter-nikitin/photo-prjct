import logging
import os
import signal
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

from selfie_search.services.face_search_queue import (
    recover_missing_payloads,
    recover_processing,
    run_once,
)

logger = logging.getLogger(__name__)

MATCHING_WATCHDOG_SECONDS = 150


def attempt_timeout(signum, frame):
    os._exit(124)


def terminate_immediately(signum, frame):
    os._exit(0)


class Command(BaseCommand):
    help = "Run the single face-search executor; recover interrupted work on startup."

    def handle(self, *args, **options):
        signal.signal(signal.SIGTERM, terminate_immediately)
        signal.signal(signal.SIGALRM, attempt_timeout)
        connection.settings_dict.setdefault("OPTIONS", {}).update(
            connect_timeout=5,
            options="-c statement_timeout=30000 -c lock_timeout=1000",
        )
        startup_complete = False
        delay = 2
        while True:
            try:
                signal.alarm(MATCHING_WATCHDOG_SECONDS)
                if not startup_complete:
                    recover_processing()
                    recover_missing_payloads()
                    startup_complete = True
                worked = run_once()
            except DatabaseError:
                logger.error("Face-search database unavailable")
                connection.close()
                if startup_complete:
                    # Restart so interrupted processing is recovered before another claim.
                    raise SystemExit(1) from None
                worked = False
            finally:
                signal.alarm(0)
            if worked:
                delay = 2
            else:
                time.sleep(delay)
                delay = min(delay * 2, 30)
