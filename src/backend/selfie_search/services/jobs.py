"""Lease, callback, cleanup, and idempotency transitions for temporary selfie work."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from processing.contracts import SELFIE_ATTEMPT_PREFIX

from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
    SelfieSearchJob,
    SelfieSearchResult,
)
from selfie_search.observability import (
    SelfieEventName,
    emit_selfie_event,
    emit_selfie_observability_failure,
)
from selfie_search.services.cluster_expansion import (
    RankedPhotoExpansion,
    direct_only_ranked_photos,
    expand_ranked_photos,
)
from selfie_search.services.ranking import (
    QueryVectorError,
    RankingError,
    rank_embeddings,
    validate_query_vector,
)
from selfie_search.services.submission import compatible_search_candidates

logger = logging.getLogger(__name__)

SELFIE_QUERY_CONTRACT_VERSION = 1
SELFIE_QUERY_PROCESSOR_TYPE = "selfie_query"
SELFIE_QUERY_PROCESSOR_VERSION = 2
DEFAULT_LEASE_SECONDS = 120
DEFAULT_RECOVERY_LIMIT = 25
MAX_ATTEMPTS = 3
_RETRY_POLICY = {
    "max_attempts": MAX_ATTEMPTS,
    "base_backoff_seconds": 30,
    "max_backoff_seconds": 300,
    "jitter_seconds": 5,
    "lease_max_seconds": 300,
}


class _CleanupStorage(Protocol):
    def delete(self, *, key: str) -> None: ...


class CleanupPending(RuntimeError):
    """The accepted outcome is durable but its private selfie has not yet been deleted."""


class SearchCompletionConflict(ValueError):
    """A terminal selfie attempt already has a different hash-only callback receipt."""


@dataclass(frozen=True)
class EmptySearchClaim:
    suggested_delay_seconds: int = 5

    @property
    def empty(self) -> bool:
        return True


@dataclass(frozen=True)
class ClaimedSearchJob:
    job: SelfieSearchJob
    attempt: SelfieSearchAttempt

    @property
    def empty(self) -> bool:
        return False


@dataclass(frozen=True)
class SearchAttemptCompletion:
    attempt: SelfieSearchAttempt
    idempotent: bool = False
    stale: bool = False


def search_attempt_reference(attempt: SelfieSearchAttempt | UUID) -> str:
    """Return the explicit transport namespace used to distinguish selfie attempt aliases."""
    identifier = attempt if isinstance(attempt, UUID) else attempt.id
    return f"{SELFIE_ATTEMPT_PREFIX}{identifier}"


def is_search_attempt_reference(value: str) -> bool:
    return value.startswith(SELFIE_ATTEMPT_PREFIX)


def parse_search_attempt_reference(value: str) -> UUID | None:
    if not is_search_attempt_reference(value):
        return None
    try:
        return UUID(value.removeprefix(SELFIE_ATTEMPT_PREFIX))
    except ValueError:
        return None


def claim_search_job(
    *,
    contract_version: int,
    processor_type: str,
    processor_version: int,
    worker_build: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: timezone.datetime | None = None,
) -> ClaimedSearchJob | EmptySearchClaim:
    """Lease one ready selfie job without changing the photo-processing queue."""
    now = now or timezone.now()
    if not _processor_matches(contract_version, processor_type, processor_version):
        return EmptySearchClaim()
    with transaction.atomic():
        job = (
            SelfieSearchJob.objects.select_for_update(skip_locked=True)
            .select_related("search")
            .filter(
                status__in=(SelfieSearchJob.Status.QUEUED, SelfieSearchJob.Status.RETRY_WAIT),
                available_at__lte=now,
                search__status=SelfieSearch.Status.QUEUED,
                search__temporary_object_key__gt="",
            )
            .order_by("available_at", "created_at", "id")
            .first()
        )
        if job is None:
            return EmptySearchClaim()
        _validate_lease_seconds(lease_seconds, job.search)
        attempt = SelfieSearchAttempt.objects.create(
            job=job,
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        job.status = SelfieSearchJob.Status.PROCESSING
        job.claimed_at = now
        job.save(update_fields=["status", "claimed_at"])
        search = job.search
        search.status = SelfieSearch.Status.PROCESSING
        search.state_changed_at = now
        search.save(update_fields=["status", "state_changed_at"])
        return ClaimedSearchJob(job=job, attempt=attempt)


def heartbeat_search_attempt(
    attempt_id: UUID,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: timezone.datetime | None = None,
) -> SelfieSearchAttempt | None:
    now = now or timezone.now()
    with transaction.atomic():
        search, job, attempt = _locked_context(attempt_id)
        _validate_lease_seconds(lease_seconds, search)
        if not _owns_current_lease(search, job, attempt, now):
            return None
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.save(update_fields=["heartbeat_at", "lease_expires_at"])
        return attempt


def refresh_search_download(
    attempt_id: UUID, *, now: timezone.datetime | None = None
) -> SelfieSearchAttempt | None:
    now = now or timezone.now()
    with transaction.atomic():
        search, job, attempt = _locked_context(attempt_id)
        return attempt if _owns_current_lease(search, job, attempt, now) else None


def complete_search_attempt(
    attempt_id: UUID,
    *,
    result: dict[str, Any],
    storage: _CleanupStorage,
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
    now: timezone.datetime | None = None,
    jitter: Callable[[int, int], int] | None = None,
) -> SearchAttemptCompletion:
    """Accept a transient query, prepare hidden rows, then publish only after exact deletion."""
    now = now or timezone.now()
    payload = _success_payload(
        result=result,
        download_duration_ms=download_duration_ms,
        compute_duration_ms=compute_duration_ms,
        total_duration_ms=total_duration_ms,
        worker_started_at=worker_started_at,
        worker_finished_at=worker_finished_at,
    )
    payload_hash = _canonical_hash(payload)
    needs_cleanup = False
    completion: SearchAttemptCompletion
    with transaction.atomic():
        search, job, attempt = _locked_context(attempt_id)
        if attempt.status != SelfieSearchAttempt.Status.IN_PROGRESS:
            completion, needs_cleanup = _existing_completion(search, attempt, payload_hash)
        elif not _owns_current_lease(search, job, attempt, now):
            completion, needs_cleanup = _stale_or_expired(
                search, job, attempt, payload_hash, now=now, jitter=jitter
            )
        else:
            try:
                query = _query_from_result(search, result)
                cohort_started_at = perf_counter()
                candidates = compatible_search_candidates(search)
                cohort_loaded_at = perf_counter()
                ranked = rank_embeddings(search, query, candidates)
                ranked_at = perf_counter()
                has_eligible_candidates = bool(candidates)
                eligible_photo_count = len({candidate.photo_id for candidate in candidates})
                eligible_face_count = len(candidates)
                load_ms = round((cohort_loaded_at - cohort_started_at) * 1_000)
                rank_ms = round((ranked_at - cohort_loaded_at) * 1_000)
            except QueryVectorError:
                raise
            except RankingError:
                _emit_ranking_finished(
                    search=search,
                    attempt=attempt,
                    outcome="incompatible",
                    eligible_photo_count=search.eligible_photo_count,
                    eligible_face_count=search.eligible_face_count,
                    matched_photo_count=0,
                    load_ms=None,
                    rank_ms=None,
                    expansion=None,
                )
                _terminal_attempt(
                    attempt,
                    status=str(SelfieSearchAttempt.Status.FAILED),
                    payload_hash=payload_hash,
                    error_code="ranking_incompatible",
                    error_detail="failed",
                    now=now,
                    download_duration_ms=download_duration_ms,
                    compute_duration_ms=compute_duration_ms,
                    total_duration_ms=total_duration_ms,
                    worker_started_at=worker_started_at,
                    worker_finished_at=worker_finished_at,
                )
                job.status = SelfieSearchJob.Status.FAILED
                job.completed_at = now
                job.save(update_fields=["status", "completed_at"])
                _prepare_cleanup(
                    search,
                    intended_status=str(SelfieSearch.Status.FAILED),
                    failure_code="failed",
                    expansion=None,
                    now=now,
                )
                completion = SearchAttemptCompletion(attempt=attempt)
                needs_cleanup = True
            else:
                intended_status = str(
                    SelfieSearch.Status.SEARCH_UNAVAILABLE
                    if not has_eligible_candidates
                    else SelfieSearch.Status.READY
                )
                expansion = _expand_direct_ranking(search=search, ranked=ranked, query=query)
                _emit_ranking_finished(
                    search=search,
                    attempt=attempt,
                    outcome="succeeded",
                    eligible_photo_count=eligible_photo_count,
                    eligible_face_count=eligible_face_count,
                    matched_photo_count=expansion.final_matched_photo_count,
                    load_ms=load_ms,
                    rank_ms=rank_ms,
                    expansion=expansion,
                    retain_expansion_snapshot=intended_status == str(SelfieSearch.Status.READY),
                )
                _terminal_attempt(
                    attempt,
                    status=str(SelfieSearchAttempt.Status.SUCCEEDED),
                    payload_hash=payload_hash,
                    now=now,
                    download_duration_ms=download_duration_ms,
                    compute_duration_ms=compute_duration_ms,
                    total_duration_ms=total_duration_ms,
                    worker_started_at=worker_started_at,
                    worker_finished_at=worker_finished_at,
                )
                job.status = SelfieSearchJob.Status.SUCCEEDED
                job.completed_at = now
                job.save(update_fields=["status", "completed_at"])
                _prepare_cleanup(
                    search,
                    intended_status=intended_status,
                    failure_code="",
                    expansion=expansion,
                    now=now,
                )
                completion = SearchAttemptCompletion(attempt=attempt)
                needs_cleanup = True
    if needs_cleanup:
        _confirm_cleanup(attempt_id=attempt_id, storage=storage, now=now)
    return completion


def fail_search_attempt(
    attempt_id: UUID,
    *,
    error_code: str,
    retryable: bool,
    storage: _CleanupStorage,
    error_detail: str = "",
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
    now: timezone.datetime | None = None,
    jitter: Callable[[int, int], int] | None = None,
) -> SearchAttemptCompletion:
    """Record a failure without retaining a raw worker callback or selfie/query bytes."""
    now = now or timezone.now()
    payload = _failure_payload(
        error_code=error_code,
        retryable=retryable,
        download_duration_ms=download_duration_ms,
        compute_duration_ms=compute_duration_ms,
        total_duration_ms=total_duration_ms,
        worker_started_at=worker_started_at,
        worker_finished_at=worker_finished_at,
    )
    payload_hash = _canonical_hash(payload)
    needs_cleanup = False
    completion: SearchAttemptCompletion
    with transaction.atomic():
        search, job, attempt = _locked_context(attempt_id)
        if attempt.status != SelfieSearchAttempt.Status.IN_PROGRESS:
            completion, needs_cleanup = _existing_completion(search, attempt, payload_hash)
        elif not _owns_current_lease(search, job, attempt, now):
            completion, needs_cleanup = _stale_or_expired(
                search, job, attempt, payload_hash, now=now, jitter=jitter
            )
        else:
            _terminal_attempt(
                attempt,
                status=str(SelfieSearchAttempt.Status.FAILED),
                payload_hash=payload_hash,
                error_code=error_code,
                error_detail=_safe_failure_detail(error_code, error_detail),
                now=now,
                download_duration_ms=download_duration_ms,
                compute_duration_ms=compute_duration_ms,
                total_duration_ms=total_duration_ms,
                worker_started_at=worker_started_at,
                worker_finished_at=worker_finished_at,
            )
            if retryable:
                needs_cleanup = _transition_retry_or_cleanup(
                    search, job, attempt, now=now, jitter=jitter
                )
            else:
                job.status = SelfieSearchJob.Status.FAILED
                job.completed_at = now
                job.save(update_fields=["status", "completed_at"])
                intended_status, failure_code = _terminal_failure_status(error_code)
                _prepare_cleanup(
                    search,
                    intended_status=intended_status,
                    failure_code=failure_code,
                    expansion=None,
                    now=now,
                )
                needs_cleanup = True
            completion = SearchAttemptCompletion(attempt=attempt)
    if needs_cleanup:
        _confirm_cleanup(attempt_id=attempt_id, storage=storage, now=now)
    return completion


def recover_expired_search_attempts(
    *,
    storage: _CleanupStorage,
    now: timezone.datetime | None = None,
    jitter: Callable[[int, int], int] | None = None,
    limit: int = DEFAULT_RECOVERY_LIMIT,
) -> list[SelfieSearchAttempt]:
    """Recover bounded expired work and retry already-persisted cleanup safely."""
    if limit < 1:
        raise ValueError("limit must be positive")
    now = now or timezone.now()
    for pending_attempt_id in (
        SelfieSearchAttempt.objects.filter(job__search__status=SelfieSearch.Status.CLEANUP_PENDING)
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    ):
        try:
            _confirm_cleanup(attempt_id=pending_attempt_id, storage=storage, now=now)
        except CleanupPending:
            pass
    candidate_ids = list(
        SelfieSearchAttempt.objects.filter(
            status=SelfieSearchAttempt.Status.IN_PROGRESS,
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    recovered: list[SelfieSearchAttempt] = []
    cleanup_attempt_ids: list[UUID] = []
    for attempt_id in candidate_ids:
        with transaction.atomic():
            search, job, attempt = _locked_context(attempt_id)
            if (
                attempt.status != SelfieSearchAttempt.Status.IN_PROGRESS
                or attempt.lease_expires_at is None
                or attempt.lease_expires_at > now
            ):
                continue
            payload_hash = _canonical_hash(
                {"outcome": "failure", "error_code": "lease_expired", "retryable": True}
            )
            _terminal_attempt(
                attempt,
                status=str(SelfieSearchAttempt.Status.EXPIRED),
                payload_hash=payload_hash,
                error_code="lease_expired",
                error_detail="",
                now=now,
            )
            if _transition_retry_or_cleanup(search, job, attempt, now=now, jitter=jitter):
                cleanup_attempt_ids.append(attempt.id)
            recovered.append(attempt)
    for attempt_id in cleanup_attempt_ids:
        try:
            _confirm_cleanup(attempt_id=attempt_id, storage=storage, now=now)
        except CleanupPending:
            pass
    return recovered


def selfie_worker_configuration(search: SelfieSearch) -> dict[str, object]:
    """Build the strict worker union configuration from the search's frozen contract snapshot."""
    configuration = search.configuration
    if not isinstance(configuration, dict):
        raise ValueError("search configuration is invalid")
    model = configuration.get("embedding_model")
    dimensions = configuration.get("embedding_dimensions")
    if model == "sface" and dimensions == 128:
        terminal_result_max_bytes = 8_192
    elif model == "adaface-ir18-webface4m" and dimensions == 512:
        terminal_result_max_bytes = 16_384
    else:
        raise ValueError("search configuration is incompatible with selfie_query v2")
    worker_configuration: dict[str, object] = {
        "retry_policy": dict(_RETRY_POLICY),
        "max_cohort_size": 1,
        "report_max_bytes": 262_144,
        "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
        "selfie_query": {
            "detection_threshold": 0.5,
            "embedding_dimensions": dimensions,
            "min_face_px": 32,
            "model": model,
        },
        "worker": {
            "api_response_max_bytes": 16_384,
            "concurrency": 1,
            "heartbeat_interval_seconds": 30,
            "lease_duration_seconds": DEFAULT_LEASE_SECONDS,
            "max_input_bytes": 20 * 1024 * 1024,
            "max_pixels": 25_000_000,
            "poll_min_delay_seconds": 5,
            "terminal_result_max_bytes": terminal_result_max_bytes,
        },
    }
    if model == "adaface-ir18-webface4m":
        worker_configuration["scrfd"] = {
            "input_size": [640, 640],
            "model": "scrfd-10g-kps",
            "model_artifact_sha256": (
                "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"
            ),
            "nms_threshold": 0.4,
        }
        worker_configuration["adaface"] = {
            "alignment": "scrfd-five-landmark-112x112",
            "input_normalization": "rgb-value-over-255-minus-0.5-over-0.5",
            "model_artifact_sha256": (
                "3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b"
            ),
            "model_revision": "0dd53f188fa27968b0a1326970ebf4aeb37ce2ca",
        }
    return worker_configuration


def _locked_context(
    attempt_id: UUID,
) -> tuple[SelfieSearch, SelfieSearchJob, SelfieSearchAttempt]:
    identity = SelfieSearchAttempt.objects.only("job_id").get(pk=attempt_id)
    job = (
        SelfieSearchJob.objects.select_for_update().select_related("search").get(pk=identity.job_id)
    )
    search = SelfieSearch.objects.select_for_update().get(pk=job.search_id)
    attempt = SelfieSearchAttempt.objects.select_for_update().get(pk=attempt_id)
    return search, job, attempt


def _processor_matches(contract_version: int, processor_type: str, processor_version: int) -> bool:
    return (
        contract_version == SELFIE_QUERY_CONTRACT_VERSION
        and processor_type == SELFIE_QUERY_PROCESSOR_TYPE
        and processor_version == SELFIE_QUERY_PROCESSOR_VERSION
    )


def _validate_lease_seconds(lease_seconds: int, search: SelfieSearch) -> None:
    worker = selfie_worker_configuration(search)["worker"]
    assert isinstance(worker, dict)
    configured = worker["lease_duration_seconds"]
    if lease_seconds != configured:
        raise ValueError("lease_seconds must equal the configured lease duration")


def _owns_current_lease(
    search: SelfieSearch,
    job: SelfieSearchJob,
    attempt: SelfieSearchAttempt,
    now: timezone.datetime,
) -> bool:
    return bool(
        search.status == SelfieSearch.Status.PROCESSING
        and job.status == SelfieSearchJob.Status.PROCESSING
        and attempt.status == SelfieSearchAttempt.Status.IN_PROGRESS
        and attempt.lease_expires_at is not None
        and attempt.lease_expires_at > now
    )


def _query_from_result(search: SelfieSearch, result: dict[str, Any]) -> tuple[float, ...]:
    if not isinstance(result, dict):
        raise QueryVectorError("selfie result is invalid")
    configuration = search.configuration
    if not isinstance(configuration, dict) or result.get("model") != configuration.get(
        "embedding_model"
    ):
        raise QueryVectorError("selfie result model is incompatible")
    return validate_query_vector(search, result.get("embedding"))


def _success_payload(
    *,
    result: dict[str, Any],
    download_duration_ms: int | None,
    compute_duration_ms: int | None,
    total_duration_ms: int | None,
    worker_started_at: str | None,
    worker_finished_at: str | None,
) -> dict[str, Any]:
    return {
        "outcome": "success",
        "result": result,
        "download_duration_ms": download_duration_ms,
        "compute_duration_ms": compute_duration_ms,
        "total_duration_ms": total_duration_ms,
        "worker_started_at": worker_started_at,
        "worker_finished_at": worker_finished_at,
    }


def _failure_payload(
    *,
    error_code: str,
    retryable: bool,
    download_duration_ms: int | None,
    compute_duration_ms: int | None,
    total_duration_ms: int | None,
    worker_started_at: str | None,
    worker_finished_at: str | None,
) -> dict[str, Any]:
    return {
        "outcome": "failure",
        "error_code": error_code,
        "retryable": retryable,
        "download_duration_ms": download_duration_ms,
        "compute_duration_ms": compute_duration_ms,
        "total_duration_ms": total_duration_ms,
        "worker_started_at": worker_started_at,
        "worker_finished_at": worker_finished_at,
    }


def _terminal_attempt(
    attempt: SelfieSearchAttempt,
    *,
    status: str,
    payload_hash: str,
    now: timezone.datetime,
    error_code: str = "",
    error_detail: str = "",
    download_duration_ms: int | None = None,
    compute_duration_ms: int | None = None,
    total_duration_ms: int | None = None,
    worker_started_at: str | None = None,
    worker_finished_at: str | None = None,
) -> None:
    attempt.status = status
    attempt.terminal_at = now
    attempt.result_hash = payload_hash
    attempt.error_code = error_code
    attempt.error_detail = error_detail
    attempt.download_duration_ms = download_duration_ms
    attempt.compute_duration_ms = compute_duration_ms
    attempt.total_duration_ms = total_duration_ms
    attempt.worker_started_at = worker_started_at
    attempt.worker_finished_at = worker_finished_at
    attempt.save(
        update_fields=[
            "status",
            "terminal_at",
            "result_hash",
            "error_code",
            "error_detail",
            "download_duration_ms",
            "compute_duration_ms",
            "total_duration_ms",
            "worker_started_at",
            "worker_finished_at",
        ]
    )


def _expand_direct_ranking(
    *, search: SelfieSearch, ranked: tuple, query: tuple[float, ...]
) -> RankedPhotoExpansion:
    """Keep optional corpus reads inside the Django completion transaction."""
    if settings.SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED is not True:
        return direct_only_ranked_photos(ranked, outcome="disabled")
    try:
        with transaction.atomic():
            from processing.models import EventFaceClusterActivation

            activation = (
                EventFaceClusterActivation.objects.select_related("corpus")
                .filter(event=search.event, active=True)
                .first()
            )
            return expand_ranked_photos(search, ranked, query, activation)
    except DatabaseError:
        return direct_only_ranked_photos(ranked, outcome="corpus_unavailable")


def _verify_prepared_result_identity(search: SelfieSearch, expansion: RankedPhotoExpansion) -> None:
    if (
        len(expansion.results) != expansion.final_matched_photo_count
        or expansion.final_matched_photo_count
        != expansion.direct_matched_photo_count + expansion.cluster_expanded_photo_count
        or SelfieSearchResult.objects.filter(search=search).count()
        != expansion.final_matched_photo_count
        or SelfieSearchDirectEvidence.objects.filter(result__search=search).count()
        != expansion.direct_matched_photo_count
        or SelfieSearchResult.objects.filter(
            search=search,
            primary_source=SelfieSearchResult.PrimarySource.FACE_CLUSTER_EXPANSION,
        ).count()
        != expansion.cluster_expanded_photo_count
    ):
        raise SearchCompletionConflict("prepared selfie result identity failed")


def _verify_terminal_result_identity(search: SelfieSearch) -> None:
    if (
        search.direct_matched_photo_count is None
        or search.cluster_expanded_photo_count is None
        or search.final_matched_photo_count is None
        or search.final_matched_photo_count
        != search.direct_matched_photo_count + search.cluster_expanded_photo_count
        or SelfieSearchResult.objects.filter(search=search).count()
        != search.final_matched_photo_count
        or SelfieSearchDirectEvidence.objects.filter(result__search=search).count()
        != search.direct_matched_photo_count
        or SelfieSearchResult.objects.filter(
            search=search,
            primary_source=SelfieSearchResult.PrimarySource.FACE_CLUSTER_EXPANSION,
        ).count()
        != search.cluster_expanded_photo_count
    ):
        raise SearchCompletionConflict("terminal selfie result identity failed")


def _prepare_cleanup(
    search: SelfieSearch,
    *,
    intended_status: str,
    failure_code: str,
    expansion: RankedPhotoExpansion | None = None,
    now: timezone.datetime,
) -> None:
    if search.status == SelfieSearch.Status.CLEANUP_PENDING:
        return
    if intended_status == SelfieSearch.Status.READY:
        if expansion is None:
            raise SearchCompletionConflict("ready cleanup requires ranked results")
        result_rows = [
            SelfieSearchResult(
                search=search,
                photo_id=row.photo_id,
                primary_source=row.primary_source,
                rank=position,
            )
            for position, row in enumerate(expansion.results, start=1)
        ]
        SelfieSearchResult.objects.bulk_create(result_rows)
        result_by_photo = {row.photo_id: row for row in result_rows}
        SelfieSearchDirectEvidence.objects.bulk_create(
            [
                SelfieSearchDirectEvidence(
                    result=result_by_photo[row.photo_id],
                    detection_id=row.direct.detection_id,
                    cosine_distance=row.direct.cosine_distance,
                )
                for row in expansion.results
                if row.direct is not None
            ]
        )
        SelfieSearchClusterEvidence.objects.bulk_create(
            [
                SelfieSearchClusterEvidence(
                    result=result_by_photo[row.photo_id],
                    corpus_id=expansion.cluster_corpus_id,
                    cluster_id=evidence.cluster_id,
                    anchor_result=result_by_photo[evidence.anchor_photo_id],
                    anchor_detection_id=evidence.anchor_detection_id,
                    member_detection_id=evidence.member_detection_id,
                    representative_distance=evidence.representative_distance,
                    source_order=evidence.source_order,
                )
                for row in expansion.results
                for evidence in row.cluster_evidence
            ]
        )
        _verify_prepared_result_identity(search, expansion)
        search.final_matched_photo_count = expansion.final_matched_photo_count
        search.direct_matched_photo_count = expansion.direct_matched_photo_count
        search.cluster_expanded_photo_count = expansion.cluster_expanded_photo_count
        search.strong_anchor_count = expansion.strong_anchor_count
        search.expanded_cluster_count = expansion.expanded_cluster_count
        search.cluster_corpus_id = expansion.cluster_corpus_id
        search.cluster_corpus_version = expansion.cluster_corpus_version
        search.cluster_configuration_hash = expansion.cluster_configuration_hash
        search.cluster_expansion_outcome = expansion.outcome
    search.status = SelfieSearch.Status.CLEANUP_PENDING
    search.intended_terminal_status = intended_status
    search.failure_code = failure_code
    search.state_changed_at = now
    search.save(
        update_fields=[
            "status",
            "intended_terminal_status",
            "failure_code",
            "state_changed_at",
            "final_matched_photo_count",
            "direct_matched_photo_count",
            "cluster_expanded_photo_count",
            "strong_anchor_count",
            "expanded_cluster_count",
            "cluster_corpus",
            "cluster_corpus_version",
            "cluster_configuration_hash",
            "cluster_expansion_outcome",
        ]
    )


def _confirm_cleanup(
    *,
    attempt_id: UUID,
    storage: _CleanupStorage,
    now: timezone.datetime,
) -> None:
    with transaction.atomic():
        search, _job, _attempt = _locked_context(attempt_id)
        if search.status != SelfieSearch.Status.CLEANUP_PENDING:
            return
        key = search.temporary_object_key
    if key:
        try:
            storage.delete(key=key)
        except StorageUnavailable as error:
            raise CleanupPending() from error
    with transaction.atomic():
        search, _job, _attempt = _locked_context(attempt_id)
        if search.status != SelfieSearch.Status.CLEANUP_PENDING:
            return
        intended_status = search.intended_terminal_status
        if intended_status not in _terminal_statuses():
            raise SearchCompletionConflict("cleanup has no terminal target")
        search.status = intended_status
        search.temporary_object_key = ""
        search.cleanup_confirmed_at = now
        search.terminal_at = now
        search.state_changed_at = now
        if intended_status == SelfieSearch.Status.READY:
            if search.final_matched_photo_count is None:
                raise SearchCompletionConflict("ready cleanup has no final count")
            _verify_terminal_result_identity(search)
            search.matched_photo_count = search.final_matched_photo_count
        else:
            search.matched_photo_count = 0
        search.save(
            update_fields=[
                "status",
                "temporary_object_key",
                "cleanup_confirmed_at",
                "terminal_at",
                "state_changed_at",
                "matched_photo_count",
            ]
        )
        transaction.on_commit(lambda: _emit_search_terminal(search=search, job=_job, now=now))


def _emit_ranking_finished(
    *,
    search: SelfieSearch,
    attempt: SelfieSearchAttempt,
    outcome: str,
    eligible_photo_count: int,
    eligible_face_count: int,
    matched_photo_count: int,
    load_ms: int | None,
    rank_ms: int | None,
    expansion: RankedPhotoExpansion | None,
    retain_expansion_snapshot: bool = True,
) -> None:
    if expansion is None:
        direct_matched_photo_count = 0
        cluster_expanded_photo_count = 0
        final_matched_photo_count = 0
        strong_anchor_count = 0
        expanded_cluster_count = 0
        cluster_corpus_version = None
        cluster_configuration_hash = None
        cluster_expansion_ms = None
        cluster_expansion_outcome = (
            "disabled"
            if settings.SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED is not True
            else "corpus_unavailable"
        )
    else:
        direct_matched_photo_count = expansion.direct_matched_photo_count
        cluster_expanded_photo_count = expansion.cluster_expanded_photo_count
        final_matched_photo_count = expansion.final_matched_photo_count
        strong_anchor_count = expansion.strong_anchor_count
        expanded_cluster_count = expansion.expanded_cluster_count
        cluster_corpus_version = (
            expansion.cluster_corpus_version if retain_expansion_snapshot else None
        )
        cluster_configuration_hash = (
            expansion.cluster_configuration_hash if retain_expansion_snapshot else None
        )
        cluster_expansion_ms = (
            expansion.duration_ms
            if retain_expansion_snapshot
            and expansion.outcome in {"expanded", "no_strong_anchor", "no_new_photos"}
            else None
        )
        cluster_expansion_outcome = expansion.outcome
    emit_selfie_event(
        logger,
        event=SelfieEventName.RANKING_FINISHED,
        event_id=search.event_id,
        search_id=search.id,
        attempt_id=attempt.id,
        outcome=outcome,
        eligible_photo_count=eligible_photo_count,
        eligible_face_count=eligible_face_count,
        matched_photo_count=matched_photo_count,
        load_ms=load_ms,
        rank_ms=rank_ms,
        direct_matched_photo_count=direct_matched_photo_count,
        cluster_expanded_photo_count=cluster_expanded_photo_count,
        final_matched_photo_count=final_matched_photo_count,
        strong_anchor_count=strong_anchor_count,
        expanded_cluster_count=expanded_cluster_count,
        cluster_corpus_version=cluster_corpus_version,
        cluster_configuration_hash=cluster_configuration_hash,
        cluster_expansion_ms=cluster_expansion_ms,
        cluster_expansion_outcome=cluster_expansion_outcome,
        configuration_hash=(
            search.configuration_hash
            if len(search.configuration_hash) == 64
            else _canonical_hash(search.configuration)
        ),
    )


def _emit_search_terminal(
    *, search: SelfieSearch, job: SelfieSearchJob, now: timezone.datetime
) -> None:
    try:
        elapsed_ms = max(0, round((now - search.created_at).total_seconds() * 1_000))
        ready = search.status == SelfieSearch.Status.READY
        emit_selfie_event(
            logger,
            event=SelfieEventName.SEARCH_TERMINAL,
            event_id=search.event_id,
            search_id=search.id,
            status=search.status,
            matched_photo_count=search.matched_photo_count,
            attempt_count=_terminal_attempt_count(job),
            elapsed_ms=elapsed_ms,
            failure_code=search.failure_code,
            cleanup_confirmed=True,
            direct_matched_photo_count=(search.direct_matched_photo_count or 0) if ready else 0,
            cluster_expanded_photo_count=(search.cluster_expanded_photo_count or 0) if ready else 0,
            cluster_corpus_version=search.cluster_corpus_version if ready else None,
            cluster_configuration_hash=search.cluster_configuration_hash if ready else None,
        )
    except Exception:
        emit_selfie_observability_failure(logger)


def _terminal_attempt_count(job: SelfieSearchJob) -> int:
    return SelfieSearchAttempt.objects.filter(job=job).count()


def _existing_completion(
    search: SelfieSearch, attempt: SelfieSearchAttempt, payload_hash: str
) -> tuple[SearchAttemptCompletion, bool]:
    if attempt.status == SelfieSearchAttempt.Status.EXPIRED:
        # A late worker may still hold the raw query.  Its receipt is deliberately not retained,
        # because this search's retry state already owns the only durable outcome.
        return SearchAttemptCompletion(attempt=attempt, stale=True), False
    if attempt.result_hash != payload_hash:
        raise SearchCompletionConflict("a different terminal payload was already recorded")
    return (
        SearchAttemptCompletion(
            attempt=attempt,
            idempotent=True,
            stale=attempt.status
            in (SelfieSearchAttempt.Status.EXPIRED, SelfieSearchAttempt.Status.STALE),
        ),
        search.status == SelfieSearch.Status.CLEANUP_PENDING,
    )


def _stale_or_expired(
    search: SelfieSearch,
    job: SelfieSearchJob,
    attempt: SelfieSearchAttempt,
    payload_hash: str,
    *,
    now: timezone.datetime,
    jitter: Callable[[int, int], int] | None,
) -> tuple[SearchAttemptCompletion, bool]:
    if attempt.lease_expires_at is not None and attempt.lease_expires_at <= now:
        _terminal_attempt(
            attempt,
            status=str(SelfieSearchAttempt.Status.EXPIRED),
            payload_hash=_canonical_hash(
                {"outcome": "failure", "error_code": "lease_expired", "retryable": True}
            ),
            error_code="lease_expired",
            error_detail="",
            now=now,
        )
        needs_cleanup = _transition_retry_or_cleanup(search, job, attempt, now=now, jitter=jitter)
        return SearchAttemptCompletion(attempt=attempt, stale=True), needs_cleanup
    _terminal_attempt(
        attempt,
        status=str(SelfieSearchAttempt.Status.STALE),
        payload_hash=payload_hash,
        now=now,
    )
    return SearchAttemptCompletion(attempt=attempt, stale=True), False


def _transition_retry_or_cleanup(
    search: SelfieSearch,
    job: SelfieSearchJob,
    attempt: SelfieSearchAttempt,
    *,
    now: timezone.datetime,
    jitter: Callable[[int, int], int] | None,
) -> bool:
    attempts_used = SelfieSearchAttempt.objects.filter(job=job).count()
    if attempts_used < _RETRY_POLICY["max_attempts"]:
        retry_at = now + timedelta(seconds=_backoff_seconds(attempts_used, jitter))
        job.status = SelfieSearchJob.Status.RETRY_WAIT
        job.available_at = retry_at
        job.save(update_fields=["status", "available_at"])
        search.status = SelfieSearch.Status.QUEUED
        search.state_changed_at = now
        search.save(update_fields=["status", "state_changed_at"])
        return False
    job.status = SelfieSearchJob.Status.FAILED
    job.completed_at = now
    job.save(update_fields=["status", "completed_at"])
    _prepare_cleanup(
        search,
        intended_status=str(SelfieSearch.Status.FAILED),
        failure_code="failed",
        expansion=None,
        now=now,
    )
    return True


def _backoff_seconds(attempts_used: int, jitter: Callable[[int, int], int] | None) -> int:
    base = min(
        _RETRY_POLICY["max_backoff_seconds"],
        _RETRY_POLICY["base_backoff_seconds"] * 2 ** (attempts_used - 1),
    )
    offset = (jitter or random.randint)(0, _RETRY_POLICY["jitter_seconds"])
    return min(
        _RETRY_POLICY["max_backoff_seconds"],
        base + max(0, min(offset, _RETRY_POLICY["jitter_seconds"])),
    )


def _terminal_failure_status(error_code: str) -> tuple[str, str]:
    mapping = {
        "no_face_detected": (str(SelfieSearch.Status.NO_FACE), "no_face"),
        "multiple_faces_detected": (str(SelfieSearch.Status.MULTIPLE_FACES), "multiple_faces"),
        "quality_rejected": (str(SelfieSearch.Status.QUALITY_REJECTED), "quality_rejected"),
    }
    return mapping.get(error_code, (str(SelfieSearch.Status.FAILED), "failed"))


def _safe_failure_detail(error_code: str, detail: str) -> str:
    # The durable error stays canonical.  Callers may only pass a bounded already-sanitized detail.
    if not isinstance(detail, str) or len(detail) > 512 or not detail:
        return _terminal_failure_status(error_code)[1]
    return detail


def _terminal_statuses() -> set[str]:
    return {
        str(SelfieSearch.Status.READY),
        str(SelfieSearch.Status.NO_FACE),
        str(SelfieSearch.Status.MULTIPLE_FACES),
        str(SelfieSearch.Status.QUALITY_REJECTED),
        str(SelfieSearch.Status.SEARCH_UNAVAILABLE),
        str(SelfieSearch.Status.FAILED),
    }


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
