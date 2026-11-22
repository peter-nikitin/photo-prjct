import logging
import signal
import time

import psycopg
from config.photo_import_metrics import PhotoImportCounters
from config.queue_notifications import listen, wait_for_notifications
from config.queue_telemetry import QueueTelemetry
from config.queue_telemetry_protocol import CHANNEL
from config.queue_work_metrics import QueueWorkCounters
from django.core.management.base import BaseCommand, CommandParser
from django.db import connection
from prometheus_client import CollectorRegistry, start_http_server

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Export queue completion and work metrics."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--port", type=int, default=9188)

    def handle(self, *args, **options) -> None:
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        telemetry = QueueTelemetry()
        registry = CollectorRegistry()
        registry.register(telemetry)
        registry.register(PhotoImportCounters())
        registry.register(QueueWorkCounters())
        connection.settings_dict["OPTIONS"] |= {"connect_timeout": 2}

        server, _thread = start_http_server(options["port"], registry=registry)
        try:
            while True:
                try:
                    with listen(CHANNEL, application_name="findme-queue-telemetry") as listener:
                        telemetry.set_listening(True)
                        while True:
                            for notification in wait_for_notifications(listener):
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
