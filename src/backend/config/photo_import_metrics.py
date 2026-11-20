"""Scrape persisted photo import gauges independently of the completion listener."""

import logging
from collections.abc import Iterator

from django.db import DatabaseError, connection, transaction
from processing.models import PhotoImportCounter
from prometheus_client.core import GaugeMetricFamily, Metric

from config.queue_telemetry_protocol import PHOTO_IMPORT_QUEUES

logger = logging.getLogger(__name__)


class PhotoImportCounters:
    def collect(self) -> Iterator[Metric]:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 100")
                rows = list(
                    PhotoImportCounter.objects.filter(
                        queue__in=PHOTO_IMPORT_QUEUES, initialized=True
                    ).values_list("queue", "pending", "failed")
                )
        except DatabaseError:
            logger.warning("Could not read photo import counters")
            rows = []
        finally:
            # Prometheus creates a request thread for each scrape, outside Django's lifecycle.
            connection.close()

        ready = len(rows) == len(PHOTO_IMPORT_QUEUES)
        yield GaugeMetricFamily(
            "findme_photo_import_counters_ready",
            "Whether the photo import backlog gauges are initialized and available.",
            value=int(ready),
        )
        if not ready:
            return

        pending_gauge = GaugeMetricFamily(
            "findme_photo_import_pending_count",
            "Approximate count of photos with an unfinished import path.",
            labels=["queue"],
        )
        failed_gauge = GaugeMetricFamily(
            "findme_photo_import_failed_count",
            "Approximate count of photos blocked by a permanent processing failure.",
            labels=["queue"],
        )
        for queue, pending, failed in rows:
            pending = max(0, pending)
            pending_gauge.add_metric([queue], pending)
            failed_gauge.add_metric([queue], min(pending, max(0, failed)))
        yield pending_gauge
        yield failed_gauge
