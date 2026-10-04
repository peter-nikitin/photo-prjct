"""Bounded, read-only aggregates for the two disjoint worker queues."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db.models import Count, Exists, Min, OuterRef, Q, QuerySet
from django.utils import timezone
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
    SelfieSearchJob,
    SelfieSearchResult,
)

from processing.auth import worker_endpoint_enabled
from processing.models import (
    BibReading,
    EventProcessingRun,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

Identity = tuple[int, str, int]
BULK_IDENTITIES: tuple[Identity, ...] = (
    (1, "capture_metadata", 2),
    (2, "generate_preview", 1),
    (2, "generate_watermarked_preview", 1),
    (2, "face_embedding", 3),
    (3, "face_embedding", 5),
    (1, "bib_recognition", 1),
)
SELFIE_IDENTITIES: tuple[Identity, ...] = ((1, "selfie_query", 2),)


def identity_setting(identities: tuple[Identity, ...]) -> str:
    return ",".join(
        f"{contract}/{processor}/{version}" for contract, processor, version in identities
    )


def parse_pool_identities(value: str, allowed: tuple[Identity, ...]) -> tuple[Identity, ...]:
    """Accept only nonempty, duplicate-free subsets of a fixed pool capability set."""
    known = {identity_setting((identity,)): identity for identity in allowed}
    supplied = value.split(",")
    if not supplied or len(supplied) > len(allowed) or len(set(supplied)) != len(supplied):
        raise ValueError("pool identities must be a nonempty unique subset of the pool allowlist")
    if any(identity not in known for identity in supplied):
        raise ValueError("pool identities must belong to the fixed pool allowlist")
    return tuple(known[identity] for identity in supplied)


def _counts(queryset: QuerySet[Any], statuses: list[str]) -> dict[str, int]:
    return queryset.aggregate(
        **{status: Count("pk", filter=Q(status=status)) for status in statuses}
    )


def _queue_counts(
    jobs: QuerySet[Any],
    attempts: QuerySet[Any],
    current: QuerySet[Any],
    *,
    now: datetime,
    statuses: list[str],
    claimable: QuerySet[Any],
) -> dict[str, Any]:
    oldest = claimable.aggregate(oldest=Min("available_at"))["oldest"]
    return {
        "jobs": _counts(jobs, statuses),
        "attempts": _counts(attempts, list(ProcessingAttempt.Status.values)),
        "claimable": claimable.count(),
        "oldest_claimable_age_seconds": max(0.0, (now - oldest).total_seconds()) if oldest else 0.0,
        "retries": jobs.filter(status="retry_wait").aggregate(
            due=Count("pk", filter=Q(available_at__lte=now)),
            future=Count("pk", filter=Q(available_at__gt=now)),
        ),
        "leases": current.filter(status="in_progress").aggregate(
            active=Count("pk", filter=Q(lease_expires_at__gt=now)),
            expired=Count("pk", filter=Q(lease_expires_at__lte=now)),
            missing_expiry=Count("pk", filter=Q(lease_expires_at__isnull=True)),
        ),
    }


def _identity_filter(identity: Identity) -> Q:
    contract, processor, version = identity
    return Q(contract_version=contract, processor_type=processor, processor_version=version)


def _bulk(identity: Identity, now: datetime) -> dict[str, Any]:
    jobs = ProcessingJob.objects.filter(_identity_filter(identity))
    attempts = ProcessingAttempt.objects.filter(_identity_filter(identity))
    current_job = PhotoProcessingState.objects.filter(
        current_job_id=OuterRef("pk"),
        photo_id=OuterRef("photo_id"),
        processor_type=OuterRef("processor_type"),
    )
    # These are the durable predicates in claim_job; enrollment switches do not revoke jobs.
    claimable = jobs.filter(
        Exists(current_job),
        status__in=("queued", "retry_wait"),
        available_at__lte=now,
        run__status__in=(EventProcessingRun.Status.COLLECTING, EventProcessingRun.Status.SEALED),
    )
    current_attempt = PhotoProcessingState.objects.filter(
        current_attempt_id=OuterRef("pk"),
        current_job_id=OuterRef("job_id"),
        photo_id=OuterRef("photo_id"),
        processor_type=OuterRef("processor_type"),
    )
    report = _queue_counts(
        jobs,
        attempts,
        attempts.filter(Exists(current_attempt)),
        now=now,
        statuses=list(ProcessingJob.Status.values),
        claimable=claimable,
    )
    report["artifacts"] = {
        "accepted_attempts": attempts.filter(accepted=True).count(),
        "current_accepted_attempts": attempts.filter(accepted_states__isnull=False)
        .distinct()
        .count(),
        "derivatives": PhotoDerivative.objects.filter(accepted_attempt__in=attempts).count(),
        "face_artifacts": FaceProcessingAttemptArtifact.objects.filter(
            attempt__in=attempts
        ).count(),
        "face_detections": PhotoFaceDetection.objects.filter(attempt__in=attempts).count(),
        "face_embeddings": FaceEmbeddingVector.objects.filter(
            detection__attempt__in=attempts
        ).count(),
        "bib_readings": BibReading.objects.filter(source_attempt__in=attempts).count(),
    }
    return report


def _selfie(now: datetime) -> dict[str, Any]:
    jobs = SelfieSearchJob.objects.all()
    attempts = SelfieSearchAttempt.objects.all()
    # SelfieSearchJob has no processor fields: its store has one supported transport identity.
    claimable = jobs.filter(
        status__in=("queued", "retry_wait"),
        available_at__lte=now,
        search__status=SelfieSearch.Status.QUEUED,
        search__temporary_object_key__gt="",
    )
    report = _queue_counts(
        jobs,
        attempts,
        attempts.filter(job__status="processing", job__search__status="processing"),
        now=now,
        statuses=list(SelfieSearchJob.Status.values),
        claimable=claimable,
    )
    report["artifacts"] = {
        "accepted_attempts": attempts.filter(status="succeeded").count(),
        "results": SelfieSearchResult.objects.count(),
        "direct_evidence": SelfieSearchDirectEvidence.objects.count(),
        "cluster_evidence": SelfieSearchClusterEvidence.objects.count(),
        "published_results": SelfieSearchResult.objects.filter(search__status="ready").count(),
    }
    return report


def _pool(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "claimable": sum(row["claimable"] for row in rows),
        "identities": rows,
        "oldest_claimable_age_seconds": max(row["oldest_claimable_age_seconds"] for row in rows),
    }
    for section in ("jobs", "attempts", "retries", "leases", "artifacts"):
        totals[section] = {key: sum(row[section][key] for row in rows) for key in rows[0][section]}
    return totals


def build_worker_pool_state(
    *,
    bulk_identities: tuple[Identity, ...] = BULK_IDENTITIES,
    selfie_identities: tuple[Identity, ...] = SELFIE_IDENTITIES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Observe durable readiness, with endpoint availability reported independently."""
    bulk_identities = parse_pool_identities(identity_setting(bulk_identities), BULK_IDENTITIES)
    selfie_identities = parse_pool_identities(
        identity_setting(selfie_identities), SELFIE_IDENTITIES
    )
    now = now or timezone.now()
    bulk_rows = [
        {"identity": identity_setting((identity,)), **_bulk(identity, now)}
        for identity in bulk_identities
    ]
    selfie_rows = [
        {"identity": identity_setting((identity,)), **_selfie(now)}
        for identity in selfie_identities
    ]
    assigned = Q()
    for identity in bulk_identities:
        assigned |= _identity_filter(identity)
    pools = {"bulk": _pool(bulk_rows), "selfie": _pool(selfie_rows)}
    return {
        "schema_version": 1,
        "observed_at": now.isoformat(),
        "endpoint_enabled": worker_endpoint_enabled(),
        "empty": not ProcessingJob.objects.exists() and not SelfieSearchJob.objects.exists(),
        "pools": pools,
        "unassigned_processing_jobs": ProcessingJob.objects.exclude(assigned).count(),
        "unassigned_processing_attempts": ProcessingAttempt.objects.exclude(assigned).count(),
    }
