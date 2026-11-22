"""Scrape committed work counts independently of queue execution and completion events."""

import logging
import time
from collections.abc import Iterator

from django.db import DatabaseError, connection, transaction
from processing.models import QueueWorkCounter
from prometheus_client.core import GaugeMetricFamily, Metric

from config.queue_catalog import QUEUES
from config.queue_work import PHOTO, STATES, queue_key

logger = logging.getLogger(__name__)


class QueueWorkCounters:
    def collect(self) -> Iterator[Metric]:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 100")
                rows = list(QueueWorkCounter.objects.all())
        except DatabaseError:
            logger.warning("Could not read queue work counters")
            rows = []
        finally:
            connection.close()

        ready = {row.queue for row in rows if not row.processor and row.initialized}
        counts = {
            queue_key(row.queue, row.processor): row
            for row in rows
            if row.queue in ready and (row.queue != PHOTO or row.processor)
        }
        availability = GaugeMetricFamily(
            "findme_queue_work_available",
            "Whether queue work counts are initialized and readable.",
            labels=["queue"],
        )
        observed = GaugeMetricFamily(
            "findme_queue_work_observed_timestamp_seconds",
            "Time of the queue work scrape, including unavailable reads.",
            labels=["queue"],
        )
        work = GaugeMetricFamily(
            "findme_queue_work_count",
            "Approximate count of unfinished tasks by queue and state.",
            labels=["queue", "state"],
        )
        at = time.time()
        for queue in QUEUES:
            key = queue.key
            mapped_photo = key.startswith("photo_") and key != "photo_archive"
            row = counts.get(key)
            available = row is not None or (mapped_photo and PHOTO in ready)
            availability.add_metric([key], int(available))
            observed.add_metric([key], at)
            if available:
                for state in STATES:
                    work.add_metric([key, state], max(0, getattr(row, state, 0)))
        yield availability
        yield observed
        yield work
