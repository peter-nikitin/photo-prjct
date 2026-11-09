import logging
import os
import signal
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

from selfie_search.services.gallery_queue import recover_processing, run_once

logger = logging.getLogger(__name__)


def attempt_timeout(signum, frame):
    os._exit(124)


def terminate_immediately(signum, frame):
    os._exit(0)


class Command(BaseCommand):
    help = "Run the single gallery-search executor; recover interrupted work on startup."

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
                signal.alarm(150)
                if not startup_complete:
                    recover_processing()
                    startup_complete = True
                worked = run_once()
            except DatabaseError:
                logger.exception("Gallery-search database unavailable")
                connection.close()
                if startup_complete:
                    # Restart so interrupted processing is recovered before another claim.
                    raise
                worked = False
            finally:
                signal.alarm(0)
            if worked:
                delay = 2
            else:
                time.sleep(delay)
                delay = min(delay * 2, 30)
