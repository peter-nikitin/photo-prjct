"""Best-effort backlog deltas from committed queue lifecycle transitions."""

import logging
from collections.abc import Callable
from functools import partial

from django.db import DatabaseError, connection, transaction

from config.queue_catalog import QUEUES as CATALOG
from config.queue_events import notify_queue_result

logger = logging.getLogger(__name__)

GALLERY_SEARCH = "gallery-search"
SELFIE_MATCHING = "selfie_face_search"
SELFIE_RECOGNITION = "selfie"
PHOTO = "photo"
IMPORT = "import"
EMAIL = "commerce_email"
PAYMENT = "commerce_payment"
ARCHIVE = "photo_archive"
QUEUES = (
    GALLERY_SEARCH,
    SELFIE_MATCHING,
    SELFIE_RECOGNITION,
    PHOTO,
    IMPORT,
    EMAIL,
    PAYMENT,
    ARCHIVE,
)
MATCHING_QUEUES = {"gallery": GALLERY_SEARCH, "selfie": SELFIE_MATCHING}

STATES = ("waiting", "processing", "retry_wait", "paused")
WORK_STATE = {
    "queued": "waiting",
    "pending": "waiting",
    "processing": "processing",
    "running": "processing",
    "enumerating": "processing",
    "transferring": "processing",
    "retry_wait": "retry_wait",
    "paused": "paused",
}


PANEL_KEYS = {
    GALLERY_SEARCH: "gallery_matching",
    SELFIE_MATCHING: "selfie_matching",
    SELFIE_RECOGNITION: "selfie_recognition",
    IMPORT: "disk_import",
    EMAIL: EMAIL,
    PAYMENT: PAYMENT,
    ARCHIVE: ARCHIVE,
}
CATALOG_KEYS = frozenset(queue.key for queue in CATALOG)
TERMINAL_OUTCOMES = {
    "succeeded": "success",
    "ready": "success",
    "sent": "success",
    "completed": "success",
    "failed": "failed",
    "partial": "failed",
    "cancelled": "canceled",
    "canceled": "canceled",
}


def queue_key(queue: str, processor: str = "") -> str | None:
    key = f"photo_{processor}" if queue == PHOTO else PANEL_KEYS.get(queue, queue)
    return key if key in CATALOG_KEYS else None


def schedule_transition(
    queue: str | None,
    before: str | None,
    after: str | None,
    *,
    processor: str = "",
    amount: int = 1,
    outcome: str | None = None,
    segments_ms: dict[str, int] | Callable[[], dict[str, int] | None] | None = None,
) -> None:
    old = WORK_STATE.get(before) if before is not None else None
    new = WORK_STATE.get(after) if after is not None else None
    if queue is None or old == new or not amount:
        return
    deltas = [amount if state == new else -amount if state == old else 0 for state in STATES]
    processors = ("", processor) if processor else ("",)
    transaction.on_commit(partial(_apply_delta, queue, processors, deltas), robust=True)
    key = queue_key(queue, processor)
    result = outcome or TERMINAL_OUTCOMES.get(after or "")
    if old is not None and new is None and key is not None and result is not None:
        transaction.on_commit(
            partial(_publish_result, key, result, segments_ms, amount), robust=True
        )


def _publish_result(
    queue: str,
    outcome: str,
    timings: dict[str, int] | Callable[[], dict[str, int] | None] | None,
    count: int,
) -> None:
    try:
        segments = timings() if callable(timings) else timings
    except Exception:  # An observation failure must not hide the accepted outcome.
        logger.warning("Could not record queue timings for %s", queue)
        segments = None
    notify_queue_result(queue, outcome, segments, count=count)


def _apply_delta(queue: str, processors: tuple[str, ...], deltas: list[int]) -> None:
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 100")
            cursor.execute("SET LOCAL lock_timeout = 10")
            # The aggregate precedes the typed row for every write.
            cursor.execute(
                """
                INSERT INTO processing_queueworkcounter AS counter
                    (queue, processor, waiting, processing, retry_wait, paused, initialized)
                SELECT %s, processor, %s, %s, %s, %s, false
                FROM unnest(%s::varchar[]) AS processor
                ORDER BY processor
                ON CONFLICT (queue, processor) DO UPDATE SET
                    waiting = counter.waiting + EXCLUDED.waiting,
                    processing = counter.processing + EXCLUDED.processing,
                    retry_wait = counter.retry_wait + EXCLUDED.retry_wait,
                    paused = counter.paused + EXCLUDED.paused
                """,
                [queue, *deltas, list(processors)],
            )
    except DatabaseError:
        logger.warning("Could not update queue work counter for %s", queue)
