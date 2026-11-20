"""Bounded operator enrollment and leasing of new person appearance jobs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Value
from django.db.models.functions import MD5, Concat
from django.utils import timezone
from feature_flags.models import FeatureFlag
from feature_flags.registry import APPAREL_SEARCH_EXPANSION, PERSON_APPEARANCE_PROCESSING
from picflow.models import Event

from processing.appearance import APPEARANCE_CONFIGURATION
from processing.contracts import PERSON_APPEARANCE_CONTRACT, ClaimedJob
from processing.models import (
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    PersonAppearanceDetection,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    _configuration_hash,
    _derivative_fingerprint,
    request_processor,
)
from processing.services.jobs import claim_job, fail_attempt
from processing.services.previews import preview_final_key
from processing.views import photo_claim_payload

CONTRACT = PERSON_APPEARANCE_CONTRACT
PROCESSOR = CONTRACT.processor_type
GATES = (PERSON_APPEARANCE_PROCESSING.key, APPAREL_SEARCH_EXPANSION.key)
LEASE_SECONDS = 120


class BackfillError(ValueError):
    """A fixed operator-facing error code without input data."""


def appearance_gates() -> dict[str, str]:
    states = dict(FeatureFlag.objects.filter(key__in=GATES).values_list("key", "state"))
    return {key: states.get(key, FeatureFlag.State.OFF) for key in GATES}


def require_off() -> None:
    if any(state != FeatureFlag.State.OFF for state in appearance_gates().values()):
        raise BackfillError("appearance_gates_enabled")


class AppearanceBackfill:
    def __init__(self, *, event_id: int, limit: int, processes: int, seed: int) -> None:
        if not 1 <= event_id <= 2**63 - 1 or not 1 <= limit <= 1000 or not 1 <= processes <= 6:
            raise BackfillError("invalid_batch_limits")
        if not 0 <= seed <= 2**63 - 1:
            raise BackfillError("invalid_seed")
        self.event_id = event_id
        self.limit = limit
        self.processes = processes
        self.seed = seed
        self.jobs: dict[UUID, str] = {}
        self.pending: set[UUID] = set()
        self.active: dict[UUID, UUID] = {}
        self.pointer_digest = ""

    def enroll(self) -> dict[str, Any]:
        if self.jobs:
            raise BackfillError("already_enrolled")
        started_at = timezone.now()
        with transaction.atomic():
            require_off()
            try:
                event = Event.objects.select_for_update().get(pk=self.event_id)
            except Event.DoesNotExist as error:
                raise BackfillError("unknown_event") from error
            previews = self._new_previews()
            if len(previews) != self.limit:
                raise BackfillError("insufficient_previews")

            jobs = {}
            for preview in previews:
                if preview.final_key != preview_final_key(
                    photo_id=preview.photo_id,
                    attempt_id=preview.accepted_attempt_id,
                    sha256=preview.sha256,
                ):
                    raise BackfillError("invalid_preview_pointer")
                state = request_processor(
                    preview.photo,
                    event=event,
                    processor_type=PROCESSOR,
                    contract_version=CONTRACT.contract_version,
                    processor_version=CONTRACT.processor_version,
                    configuration=APPEARANCE_CONFIGURATION,
                    input_fingerprint=_derivative_fingerprint(preview),
                )
                if state.current_job_id is None:
                    raise BackfillError("enrollment_failed")
                jobs[state.current_job_id] = preview.photo_id
            pointer_digest = self._pointer_digest(list(jobs.values()))
            require_off()

        self.jobs = jobs
        self.pending = set(jobs)
        self.pointer_digest = pointer_digest
        return {
            "manifest": [
                {"photo_id": photo_id, "job_id": str(job_id)} for job_id, photo_id in jobs.items()
            ],
            "event_id": self.event_id,
            "seed": self.seed,
            "configuration_hash": _configuration_hash(APPEARANCE_CONFIGURATION),
            "started_at": started_at,
            "enrollment_seconds": (timezone.now() - started_at).total_seconds(),
            "flags": appearance_gates(),
        }

    def _new_previews(self) -> list[PhotoDerivative]:
        states = PhotoProcessingState.objects.filter(photo_id=OuterRef("photo_id"))
        old_jobs = ProcessingJob.objects.filter(
            photo_id=OuterRef("photo_id"), processor_type=PROCESSOR
        )
        return list(
            PhotoDerivative.objects.filter(
                photo__event_id=self.event_id,
                variant="preview-small-v1",
                accepted_attempt__accepted=True,
                accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
                photo__original_size__isnull=False,
                photo__original_content_type="image/jpeg",
            )
            .exclude(photo__original_key="")
            .filter(
                Exists(
                    states.filter(
                        processor_type=GENERATE_PREVIEW_PROCESSOR,
                        status=PhotoProcessingState.Status.SUCCEEDED,
                        accepted_attempt_id=OuterRef("accepted_attempt_id"),
                    )
                ),
                ~Exists(states.filter(processor_type=PROCESSOR)),
                ~Exists(old_jobs),
            )
            .select_related("photo")
            .order_by(MD5(Concat(Value(f"{self.seed}:"), "photo_id")), "photo_id")[: self.limit]
        )

    def claim(self) -> dict[str, object]:
        if len(self.active) >= self.processes:
            raise BackfillError("pool_full")
        if not self.pending:
            raise BackfillError("no_pending_jobs")
        with transaction.atomic():
            require_off()
            claimed = claim_job(
                contract_version=CONTRACT.contract_version,
                processor_type=PROCESSOR,
                processor_version=CONTRACT.processor_version,
                lease_seconds=LEASE_SECONDS,
                event_id=self.event_id,
                job_ids=self.pending,
            )
            if not isinstance(claimed, ClaimedJob):
                return {"empty": True}
            payload = photo_claim_payload(claimed)
        self.pending.remove(claimed.job.id)
        self.active[claimed.attempt.id] = claimed.job.id
        return payload

    def result(self, attempt_id: str) -> dict[str, Any]:
        try:
            attempt_key = UUID(attempt_id)
            job_id = self.active[attempt_key]
        except (ValueError, KeyError) as error:
            raise BackfillError("unknown_attempt") from error
        attempt = ProcessingAttempt.objects.get(pk=attempt_key, job_id=job_id)
        result = _attempt_result(attempt)
        if attempt.status != ProcessingAttempt.Status.IN_PROGRESS:
            del self.active[attempt_key]
        return result

    def finish(self, *, release: bool = True) -> dict[str, Any]:
        """Read final database outcomes after the pool has exited."""
        remaining = []
        for attempt_id in list(self.active):
            attempt = ProcessingAttempt.objects.get(pk=attempt_id, job_id=self.active[attempt_id])
            if release and attempt.status == ProcessingAttempt.Status.IN_PROGRESS:
                # A crashed process has no callback. Give its existing job the normal retry policy.
                fail_attempt(attempt_id, error_code="backfill_worker_stopped", retryable=True)
            remaining.append(self.result(str(attempt_id)))
        statuses = dict(ProcessingJob.objects.filter(pk__in=self.jobs).values_list("id", "status"))
        remaining.extend(
            {
                "job_id": str(job_id),
                "photo_id": self.jobs[job_id],
                "attempt_id": None,
                "status": statuses[job_id],
                "accepted": False,
            }
            for job_id in sorted(self.pending)
        )
        return {
            "jobs": dict(Counter(statuses.values())),
            "remaining": remaining,
            "pointers_preserved": bool(self.jobs)
            and self._pointer_digest(list(self.jobs.values())) == self.pointer_digest,
            "flags": appearance_gates(),
        }

    def _pointer_digest(self, photo_ids: list[str]) -> str:
        states = PhotoProcessingState.objects.filter(photo__event_id=self.event_id)
        rows = (
            states.filter(
                Q(
                    photo_id__in=photo_ids,
                    processor_type__in=(FACE_EMBEDDING_PROCESSOR, GENERATE_PREVIEW_PROCESSOR),
                )
                | (Q(processor_type=PROCESSOR) & ~Q(photo_id__in=photo_ids))
            )
            .order_by("photo_id", "processor_type")
            .values_list("photo_id", "processor_type", "accepted_attempt_id")
        )
        digest = hashlib.sha256()
        for row in rows.iterator():
            digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        return digest.hexdigest()


def _attempt_result(attempt: ProcessingAttempt) -> dict[str, Any]:
    counts = list(
        PersonAppearanceDetection.objects.filter(attempt=attempt)
        .order_by()
        .values("reason")
        .annotate(
            people=Count("pk"),
            reid=Count("reid"),
            color=Count("color"),
            helmet=Count("helmet"),
        )
    )
    accepted = (
        attempt.accepted
        and attempt.status == ProcessingAttempt.Status.SUCCEEDED
        and PhotoProcessingState.objects.filter(
            photo_id=attempt.photo_id,
            processor_type=PROCESSOR,
            current_job_id=attempt.job_id,
            accepted_attempt_id=attempt.id,
        ).exists()
    )
    return {
        "job_id": str(attempt.job_id),
        "photo_id": attempt.photo_id,
        "attempt_id": str(attempt.id),
        "status": attempt.status,
        "accepted": accepted,
        "error_code": attempt.error_code,
        "download_ms": attempt.download_duration_ms,
        "compute_ms": attempt.compute_duration_ms,
        "total_ms": attempt.total_duration_ms,
        "claimed_at": attempt.claimed_at,
        "terminal_at": attempt.terminal_at,
        "reasons": {row["reason"]: row["people"] for row in counts},
        **{
            field: sum(row[field] for row in counts)
            for field in ("people", "reid", "color", "helmet")
        },
    }
