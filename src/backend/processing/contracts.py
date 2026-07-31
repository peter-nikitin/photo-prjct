"""Typed values exchanged by the processing worker and Django services."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from processing.models import ProcessingAttempt, ProcessingJob


@dataclass(frozen=True)
class ProcessorContract:
    processor_type: str
    contract_version: int
    processor_version: int


CAPTURE_METADATA_CONTRACT = ProcessorContract(
    processor_type="capture_metadata",
    contract_version=1,
    processor_version=1,
)
FACE_EMBEDDING_CONTRACT = ProcessorContract(
    processor_type="face_embedding",
    contract_version=1,
    processor_version=1,
)
GENERATE_PREVIEW_CONTRACT = ProcessorContract(
    processor_type="generate_preview",
    contract_version=2,
    processor_version=1,
)
PREVIEW_FACE_EMBEDDING_CONTRACT = ProcessorContract(
    processor_type="face_embedding",
    contract_version=2,
    processor_version=2,
)
SELFIE_QUERY_CONTRACT = ProcessorContract(
    processor_type="selfie_query",
    contract_version=1,
    processor_version=1,
)
SELFIE_ATTEMPT_PREFIX = "selfie_"


@dataclass(frozen=True)
class AttemptReference:
    """A route-level attempt identity that cannot silently cross work kinds."""

    kind: str
    attempt_id: UUID


def parse_attempt_reference(value: str) -> AttemptReference | None:
    """Parse a raw legacy photo UUID or an explicit ``selfie_<uuid>`` alias.

    A raw UUID remains necessary for the already-versioned worker union.  Views resolve a raw UUID
    against photo attempts first so existing photo semantics are byte-for-byte unchanged.
    """
    if value.startswith(SELFIE_ATTEMPT_PREFIX):
        try:
            return AttemptReference(
                kind="selfie",
                attempt_id=UUID(value.removeprefix(SELFIE_ATTEMPT_PREFIX)),
            )
        except ValueError:
            return None
    try:
        return AttemptReference(kind="unqualified", attempt_id=UUID(value))
    except ValueError:
        return None


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
