import logging
import signal
import time

import psycopg
from config.queue_telemetry import QueueTelemetry
from config.queue_telemetry_protocol import CHANNEL
from django.core.management.base import BaseCommand, CommandParser
from django.db import connection
from processing.photo_import_telemetry import read_photo_import_backlogs
from prometheus_client import CollectorRegistry, start_http_server
from psycopg import sql
from psycopg.rows import TupleRow

logger = logging.getLogger(__name__)
RECONCILE_INTERVAL_SECONDS = 900


def _observe_notifications(
    listener: psycopg.Connection[TupleRow], telemetry: QueueTelemetry
) -> None:
    while True:
        backlogs = read_photo_import_backlogs()
        connection.close()
        if backlogs is not None:
            telemetry.set_backlog_snapshot(backlogs, reconciled_at=time.time())
        for notification in listener.notifies(timeout=RECONCILE_INTERVAL_SECONDS):
            telemetry.record(notification.payload)


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
        try:
            while True:
                try:
                    with psycopg.connect(**params) as listener:
                        listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL)))
                        telemetry.set_listening(True)
                        _observe_notifications(listener, telemetry)
                except (psycopg.Error, OSError):
                    logger.warning("Queue telemetry PostgreSQL connection unavailable; retrying")
                finally:
                    telemetry.set_listening(False)
                time.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
