"""Best-effort backlog deltas from committed queue lifecycle transitions."""

import logging
from functools import partial

from django.db import DatabaseError, connection, transaction

logger = logging.getLogger(__name__)

GALLERY_SEARCH = "gallery-search"
SELFIE_MATCHING = "selfie_face_search"
SELFIE_RECOGNITION = "selfie"
PHOTO = "photo"
IMPORT = "import"
COMMERCE = "commerce"
ARCHIVE = "photo_archive"
QUEUES = (GALLERY_SEARCH, SELFIE_MATCHING, SELFIE_RECOGNITION, PHOTO, IMPORT, COMMERCE, ARCHIVE)
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


def schedule_transition(
    queue: str | None,
    before: str | None,
    after: str | None,
    *,
    processor: str = "",
    amount: int = 1,
) -> None:
    old = WORK_STATE.get(before) if before is not None else None
    new = WORK_STATE.get(after) if after is not None else None
    if queue is None or old == new or not amount:
        return
    deltas = [amount if state == new else -amount if state == old else 0 for state in STATES]
    processors = ("", processor) if processor else ("",)
    transaction.on_commit(partial(_apply_delta, queue, processors, deltas), robust=True)


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
