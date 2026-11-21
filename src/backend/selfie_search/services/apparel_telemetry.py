"""Bounded apparel-stage diagnostics, independent of search success."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from time import perf_counter

from django.db import DatabaseError

logger = logging.getLogger(__name__)


def record(
    stage: str, search_id: object, elapsed_ms: float, budget_ms: int, limit_ms: int, reason: str
) -> None:
    """Telemetry failures must never change the search outcome."""
    try:
        remaining = max(0, budget_ms - ceil(elapsed_ms))
        logger.info(
            json.dumps(
                dict(
                    event="apparel_stage_finished",
                    stage=stage,
                    search_id=str(search_id),
                    elapsed_ms=round(elapsed_ms, 3),
                    budget_before_ms=max(0, budget_ms),
                    budget_remaining_ms=round(remaining, 3),
                    statement_timeout_ms=max(0, limit_ms),
                    reason=reason,
                )
            )
        )
    except Exception:
        pass


@dataclass
class StageTiming:
    elapsed_ms: int = 0


@contextmanager
def stage(
    name: str,
    search_id: object,
    budget_ms: int,
    limit_ms: int,
    *,
    started: float | None = None,
) -> Iterator[StageTiming]:
    if started is None:
        started = perf_counter()
    timing = StageTiming()
    reason = "succeeded"
    try:
        yield timing
    except DatabaseError as error:
        code = getattr(error.__cause__, "sqlstate", None)
        reason = {"57014": "statement_timeout", "55P03": "lock_timeout"}.get(
            str(code), "database_error"
        )
        raise
    except ValueError:
        reason = "invalid_evidence"
        raise
    except Exception:
        reason = "unexpected_error"
        raise
    finally:
        timing.elapsed_ms = ceil((perf_counter() - started) * 1000)
        record(name, search_id, timing.elapsed_ms, budget_ms, limit_ms, reason)
