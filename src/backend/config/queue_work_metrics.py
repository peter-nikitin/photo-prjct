"""Scrape committed work counts independently of queue execution and completion events."""

import logging
from collections import Counter
from collections.abc import Iterator

from django.db import DatabaseError, connection, transaction
from processing.models import QueueWorkCounter
from prometheus_client.core import GaugeMetricFamily, Metric

from config.queue_work import PHOTO, QUEUES, STATES

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
            # Prometheus runs each HTTP scrape in its own thread.
            connection.close()

        available = {row.queue for row in rows if not row.processor and row.initialized}
        availability = GaugeMetricFamily(
            "findme_queue_work_available",
            "Whether queue work counts are initialized and readable.",
            labels=["queue"],
        )
        for queue in QUEUES:
            availability.add_metric([queue], int(queue in available))
        yield availability

        work = GaugeMetricFamily(
            "findme_queue_work_count",
            "Approximate count of unfinished tasks by queue and state.",
            labels=["queue", "state"],
        )
        processing = GaugeMetricFamily(
            "findme_processing_queue_work_count",
            "Approximate count of unfinished photo tasks by processor and state.",
            labels=["processor", "state"],
        )
        photo_totals: Counter[str] = Counter()
        for row in rows:
            if row.queue not in available:
                continue
            for state in STATES:
                value = max(0, getattr(row, state))
                if row.queue != PHOTO and not row.processor:
                    work.add_metric([row.queue, state], value)
                elif row.queue == PHOTO and row.processor:
                    processing.add_metric([row.processor, state], value)
                    photo_totals[state] += value
        if PHOTO in available:
            # The aggregate agrees with the displayed processor counts even if a missed
            # delta leaves one processor negative while others still have work.
            for state in STATES:
                work.add_metric([PHOTO, state], photo_totals[state])
        yield work
        yield processing
