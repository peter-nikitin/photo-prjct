"""Best-effort publication of queue telemetry after the product transaction."""

import json
import logging

from django.db import DatabaseError, connection, transaction

from config.queue_telemetry_protocol import (
    BACKLOG_CHANGED_EVENT,
    CHANNEL,
    COMPLETED_EVENT,
    VERSION,
    PhotoImportBacklogs,
)

logger = logging.getLogger(__name__)


def notify_queue_completion(queue: str, segments_ms: dict[str, int]) -> None:
    """Send a completion from an on_commit callback in a fresh transaction."""
    _notify(COMPLETED_EVENT, {"queue": queue, "segments_ms": segments_ms})


def notify_queue_backlog_delta(deltas: PhotoImportBacklogs) -> None:
    """Send a per-photo pending/failed delta from an on_commit callback."""
    _notify(BACKLOG_CHANGED_EVENT, {"deltas": deltas})


def _notify(event: str, fields: dict[str, object]) -> None:
    payload = json.dumps({"event": event, "version": VERSION, **fields}, separators=(",", ":"))
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 100")
            cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, payload])
    except DatabaseError:
        logger.warning("Could not publish queue telemetry event %s", event)
