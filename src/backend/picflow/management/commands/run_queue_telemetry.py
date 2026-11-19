import logging
import signal
import time
from threading import Event, Thread

import psycopg
from config.queue_telemetry import QueueTelemetry
from config.queue_telemetry_protocol import CHANNEL
from django.core.management.base import BaseCommand, CommandParser
from django.db import connection
from processing.photo_import_telemetry import (
    read_photo_import_backlog_page,
    read_photo_import_backlog_upper_bound,
)
from prometheus_client import CollectorRegistry, start_http_server
from psycopg import sql

logger = logging.getLogger(__name__)
RECONCILE_INTERVAL_SECONDS = 900
RECONCILE_RETRY_SECONDS = 30
RECONCILE_PAGE_PAUSE_SECONDS = 0.5


def _reconcile_backlogs(telemetry: QueueTelemetry, stopped: Event) -> bool:
    reconciliation = telemetry.start_backlog_reconciliation()
    if reconciliation is None:
        return True
    try:
        upper_bound = read_photo_import_backlog_upper_bound()
        if not telemetry.set_backlog_reconciliation_upper_bound(reconciliation, upper_bound):
            return True

        after = None
        while upper_bound is not None and not stopped.is_set():
            page = read_photo_import_backlog_page(after=after, upper_bound=upper_bound)
            if page is None:
                break
            after, counts = page
            if not telemetry.add_backlog_reconciliation_page(
                reconciliation, after=after, counts=counts
            ):
                return True
            if after == upper_bound or stopped.wait(RECONCILE_PAGE_PAUSE_SECONDS):
                break

        if not stopped.is_set():
            telemetry.finish_backlog_reconciliation(reconciliation, reconciled_at=time.time())
        return True
    except Exception:
        logger.exception("Photo import backlog reconciliation failed")
        return False
    finally:
        telemetry.discard_backlog_reconciliation(reconciliation)
        connection.close()


def _run_reconciliations(telemetry: QueueTelemetry, requested: Event, stopped: Event) -> None:
    delay = RECONCILE_INTERVAL_SECONDS
    while not stopped.is_set():
        requested.wait(delay)
        requested.clear()
        if stopped.is_set():
            return
        completed = _reconcile_backlogs(telemetry, stopped)
        if not completed and delay != RECONCILE_RETRY_SECONDS:
            delay = RECONCILE_RETRY_SECONDS
        else:
            delay = RECONCILE_INTERVAL_SECONDS


class Command(BaseCommand):
    help = "Export queue completion counters and approximate photo import backlogs."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--port", type=int, default=9188)

    def handle(self, *args, **options) -> None:
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        telemetry = QueueTelemetry()
        registry = CollectorRegistry()
        registry.register(telemetry)
        connection.settings_dict["OPTIONS"] |= {"connect_timeout": 2}
        params = connection.get_connection_params() | {
            "autocommit": True,
            "application_name": "findme-queue-telemetry",
            "connect_timeout": 2,
            "options": "-c statement_timeout=2000",
        }

        server, _thread = start_http_server(options["port"], registry=registry)
        requested, stopped = Event(), Event()
        worker = Thread(
            target=_run_reconciliations,
            args=(telemetry, requested, stopped),
            name="photo-import-reconciliation",
            daemon=True,
        )
        worker.start()
        try:
            while True:
                try:
                    with psycopg.connect(**params) as listener:
                        listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL)))
                        telemetry.set_listening(True)
                        requested.set()
                        for notification in listener.notifies():
                            telemetry.record(notification.payload)
                except (psycopg.Error, OSError):
                    logger.warning("Queue telemetry PostgreSQL connection unavailable; retrying")
                finally:
                    telemetry.set_listening(False)
                time.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            requested.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
