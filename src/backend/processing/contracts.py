"""Typed values exchanged by the processing worker and Django services."""

from __future__ import annotations

from dataclasses import dataclass

from processing.models import ProcessingAttempt, ProcessingJob


@dataclass(frozen=True)
class EmptyClaim:
    suggested_delay_seconds: int = 5

    @property
    def empty(self) -> bool:
        return True


@dataclass(frozen=True)
class ClaimedJob:
    job: ProcessingJob
    attempt: ProcessingAttempt

    @property
    def empty(self) -> bool:
        return False


@dataclass(frozen=True)
class AttemptCompletion:
    attempt: ProcessingAttempt
    idempotent: bool = False
    stale: bool = False


class CompletionConflict(ValueError):
    """A terminal attempt has already recorded a different payload."""

    def __init__(self, message: str, *, attempt_id=None, submitted_hash: str = "") -> None:
        super().__init__(message)
        self.attempt_id = attempt_id
        self.submitted_hash = submitted_hash
