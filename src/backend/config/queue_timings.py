"""Helpers for attributing one complete calendar interval without overlap."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from django.db import DatabaseError, connection, transaction


class TimedAttempt(Protocol):
    claimed_at: datetime | None
    download_duration_ms: int | None
    compute_duration_ms: int | None
    total_duration_ms: int | None


def elapsed_ms(start: datetime, finish: datetime) -> int:
    return round((finish - start).total_seconds() * 1000)


def worker_cycle(
    submitted: datetime,
    attempt: TimedAttempt,
    finished: datetime,
    first_claim: Callable[[], datetime | None],
) -> dict[str, int] | None:
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 100")
            first = first_claim()
    except DatabaseError:
        return None
    return worker_segments(submitted, first, attempt, finished)


def worker_segments(
    submitted: datetime,
    first: datetime | None,
    attempt: TimedAttempt,
    finished: datetime,
) -> dict[str, int] | None:
    claimed = attempt.claimed_at
    download = attempt.download_duration_ms
    compute = attempt.compute_duration_ms
    total = attempt.total_duration_ms
    if (
        first is None
        or claimed is None
        or not submitted <= first <= claimed <= finished
        or download is None
        or compute is None
        or total is None
    ):
        return None
    wait = elapsed_ms(submitted, first)
    until_final_claim = elapsed_ms(submitted, claimed)
    execution = elapsed_ms(submitted, finished) - until_final_claim
    if not 0 <= download + compute <= total <= execution:
        return None
    return {
        "wait": wait,
        "retry": until_final_claim - wait,
        "download": download,
        "compute": compute,
        "accept": execution - download - compute,
    }
