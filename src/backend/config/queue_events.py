"""Best-effort publication of queue telemetry after the product transaction."""

import json
import logging

from django.db import DatabaseError, connection, transaction

from config.queue_telemetry_protocol import (
    CHANNEL,
    COMPLETED_EVENT,
    COMPLETION_VERSION,
    RESULT_EVENT,
    RESULT_VERSION,
)

logger = logging.getLogger(__name__)


def notify_queue_completion(queue: str, segments_ms: dict[str, int]) -> None:
    """Send a completion from an on_commit callback in a fresh transaction."""
    _notify(
        {
            "event": COMPLETED_EVENT,
            "version": COMPLETION_VERSION,
            "queue": queue,
            "segments_ms": segments_ms,
        },
    )


def notify_queue_result(
    queue: str, outcome: str, segments_ms: dict[str, int] | None = None, *, count: int = 1
) -> None:
    _notify(
        {
            "event": RESULT_EVENT,
            "version": RESULT_VERSION,
            "queue": queue,
            "outcome": outcome,
            "segments_ms": segments_ms,
            "count": count,
        }
    )


def _notify(event: dict[str, object]) -> None:
    payload = json.dumps(event, separators=(",", ":"))
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 100")
            cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, payload])
    except DatabaseError:
        logger.warning("Could not publish queue telemetry event %s", event["event"])
