"""Operator-triggered initialization and repair from indexed active queue states."""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from commerce.models import EmailDelivery, PaymentAttempt
from config.queue_work import (
    ARCHIVE,
    COMMERCE,
    IMPORT,
    MATCHING_QUEUES,
    PHOTO,
    QUEUES,
    SELFIE_RECOGNITION,
    STATES,
    WORK_STATE,
)
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Model, QuerySet
from ingestion.models import ImportBatch
from picflow.models import PhotoArchiveJob
from processing.models import ProcessingJob, QueueWorkCounter
from selfie_search.models import FaceSearchTask, SelfieSearchJob

type Totals = dict[tuple[str, str], Counter[str]]


def _active_groups(rows: QuerySet[Model], *dimensions: str) -> Iterable[dict[str, Any]]:
    # Each caller supplies an active-state predicate covered by an existing index.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 250")
        cursor.execute("SET LOCAL enable_seqscan = off")
        return list(rows.values(*dimensions).annotate(total=Count("pk")).order_by())


def _active_work() -> Totals:
    totals: Totals = {(queue, ""): Counter() for queue in QUEUES}
    for row in _active_groups(
        FaceSearchTask.objects.filter(status__in=("queued", "processing")), "source", "status"
    ):
        queue = MATCHING_QUEUES.get(row["source"])
        if queue is not None:
            totals[queue, ""][WORK_STATE[row["status"]]] += row["total"]
    for row in _active_groups(
        ProcessingJob.objects.filter(status__in=("queued", "processing", "retry_wait")),
        "status",
        "processor_type",
    ):
        state, processor, count = WORK_STATE[row["status"]], row["processor_type"], row["total"]
        totals[PHOTO, ""][state] += count
        if processor:
            totals.setdefault((PHOTO, processor), Counter())[state] += count
    for queue, rows, field in (
        (
            SELFIE_RECOGNITION,
            SelfieSearchJob.objects.filter(status__in=("queued", "processing", "retry_wait")),
            "status",
        ),
        (
            IMPORT,
            ImportBatch.objects.filter(
                status__in=("queued", "enumerating", "transferring", "paused")
            ),
            "status",
        ),
        (
            COMMERCE,
            EmailDelivery.objects.filter(state__in=("pending", "processing", "retry_wait")),
            "state",
        ),
        # The pending predicate matches commerce_payment_attempt_one_pending_uniq.
        (COMMERCE, PaymentAttempt.objects.filter(status="pending"), "reconciliation_state"),
        (ARCHIVE, PhotoArchiveJob.objects.filter(status__in=("queued", "running")), "status"),
    ):
        for row in _active_groups(rows, field):
            totals[queue, ""][WORK_STATE[row[field]]] += row["total"]
    return totals


def _replace_counters(totals: Totals) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 100")
        cursor.execute("SET LOCAL lock_timeout = 10")
        existing = (
            QueueWorkCounter.objects.select_for_update()
            .order_by("queue", "processor")
            .values_list("queue", "processor")
        )
        for key in existing:
            totals.setdefault(key, Counter())
        QueueWorkCounter.objects.bulk_create(
            [
                QueueWorkCounter(
                    queue=queue,
                    processor=processor,
                    waiting=counts["waiting"],
                    processing=counts["processing"],
                    retry_wait=counts["retry_wait"],
                    paused=counts["paused"],
                    initialized=not processor,
                )
                for (queue, processor), counts in sorted(totals.items())
            ],
            update_conflicts=True,
            unique_fields=["queue", "processor"],
            update_fields=[*STATES, "initialized"],
        )


class Command(BaseCommand):
    help = "Initialize or repair approximate queue work counters from active rows."
    requires_system_checks: list[str] = []

    def handle(self, *args, **options) -> None:
        try:
            totals = _active_work()
            _replace_counters(totals)
        except DatabaseError as error:
            raise CommandError("Could not rebuild queue work counters") from error

        for (queue, processor), counts in sorted(totals.items()):
            name = f"{queue}/{processor}" if processor else queue
            values = ", ".join(f"{state}={counts[state]}" for state in STATES)
            self.stdout.write(f"{name}: {values}")
