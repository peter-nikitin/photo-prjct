"""Best-effort publication of queue telemetry after the product transaction."""

import json
import logging

from django.db import DatabaseError, connection, transaction

from config.queue_telemetry_protocol import CHANNEL, COMPLETED_EVENT, VERSION

logger = logging.getLogger(__name__)


def notify_queue_completion(queue: str, segments_ms: dict[str, int]) -> None:
    """Send a completion from an on_commit callback in a fresh transaction."""
    payload = json.dumps(
        {
            "event": COMPLETED_EVENT,
            "version": VERSION,
            "queue": queue,
            "segments_ms": segments_ms,
        },
        separators=(",", ":"),
    )
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 100")
            cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, payload])
    except DatabaseError:
        logger.warning("Could not publish queue completion telemetry")
