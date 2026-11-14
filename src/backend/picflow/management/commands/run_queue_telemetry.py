import logging
import signal
import time

import psycopg
from config.queue_telemetry import QueueTelemetry
from config.queue_telemetry_protocol import CHANNEL
from django.core.management.base import BaseCommand, CommandParser
from django.db import connection
from prometheus_client import CollectorRegistry, start_http_server
from psycopg import sql

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Export queue completion notifications as Prometheus counters."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--port", type=int, default=9188)

    def handle(self, *args, **options) -> None:
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        telemetry = QueueTelemetry()
        registry = CollectorRegistry()
        registry.register(telemetry)
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
            server.shutdown()
            server.server_close()
