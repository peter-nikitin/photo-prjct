"""Read-only ready-work observations from the same durable predicates as claims."""

from __future__ import annotations

import time
from datetime import datetime

from commerce.models import EmailDelivery, EmailDeliveryAttempt, PaymentAttempt
from commerce.tbank_gateway import TBANK_ADAPTER_KEY
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Exists, Max, Min, OuterRef, Q
from django.utils import timezone
from ingestion.models import ImportAttempt, ImportBatch, ImportItem
from ingestion.services.imports import MAX_OPERATION_ATTEMPTS, _owner_is_eligible
from processing.models import EventProcessingRun, PhotoProcessingState, ProcessingJob
from processing.services.worker_pool_state import BULK_IDENTITIES
from selfie_search.models import SelfieSearch, SelfieSearchJob

type QueueSample = tuple[int, float, float, float]


def _stamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0.0


def _sample(
    count: int, oldest: datetime | None, success: datetime | None, now: datetime
) -> QueueSample:
    age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
    return count, age, _stamp(success), time.time()


def _gallery(now: datetime) -> QueueSample:
    gallery = SelfieSearch.objects.filter(configuration__processor="gallery_photo_query")
    ready = gallery.filter(status="queued", gallery_available_at__lte=now)
    values = ready.aggregate(count=Count("pk"), oldest=Min("gallery_available_at"))
    success = gallery.filter(status="ready").aggregate(last=Max("terminal_at"))["last"]
    return _sample(values["count"], values["oldest"], success, now)


def _selfie(now: datetime) -> QueueSample | None:
    if not settings.PHOTO_PROCESSING_ENABLED:
        return _sample(0, None, None, now)
    ready = SelfieSearchJob.objects.filter(
        status__in=("queued", "retry_wait"),
        available_at__lte=now,
        search__status="queued",
        search__temporary_object_key__gt="",
    )
    values = ready.aggregate(count=Count("pk"), oldest=Min("available_at"))
    success = SelfieSearchJob.objects.filter(status="succeeded").aggregate(
        last=Max("completed_at")
    )["last"]
    return _sample(values["count"], values["oldest"], success, now)


def _photo(now: datetime) -> QueueSample | None:
    if not settings.PHOTO_PROCESSING_ENABLED:
        return _sample(0, None, None, now)
    identities = Q()
    for contract, processor, version in BULK_IDENTITIES:
        identities |= Q(
            contract_version=contract, processor_type=processor, processor_version=version
        )
    current = PhotoProcessingState.objects.filter(
        current_job_id=OuterRef("pk"),
        photo_id=OuterRef("photo_id"),
        processor_type=OuterRef("processor_type"),
    )
    jobs = ProcessingJob.objects.filter(identities)
    ready = jobs.filter(
        Exists(current),
        status__in=("queued", "retry_wait"),
        available_at__lte=now,
        run__status__in=(
            EventProcessingRun.Status.COLLECTING,
            EventProcessingRun.Status.SEALED,
        ),
    )
    values = ready.aggregate(count=Count("pk"), oldest=Min("available_at"))
    success = (
        jobs.filter(status="succeeded", completed_at__isnull=False)
        .order_by("-completed_at")
        .values_list("completed_at", flat=True)
        .first()
    )
    return _sample(values["count"], values["oldest"], success, now)


def _import(now: datetime) -> QueueSample:
    pending_item = ImportItem.objects.filter(
        batch_id=OuterRef("pk"), download_attempts__lt=MAX_OPERATION_ATTEMPTS
    ).filter(
        Q(status="pending")
        | Q(
            status__in=("claimed", "uploading"),
            attempts__kind="file",
            attempts__status="active",
            attempts__lease_expires_at__lte=now,
        )
    )
    active_attempt = ImportAttempt.objects.filter(
        batch_id=OuterRef("pk"), status="active", lease_expires_at__gt=now
    )
    batches = (
        ImportBatch.objects.filter(
            status__in=("queued", "enumerating", "transferring", "paused"),
        )
        .filter(
            Q(manifest_complete=False, manifest_attempts__lt=MAX_OPERATION_ATTEMPTS)
            | Q(
                manifest_complete=True,
                pk__in=ImportBatch.objects.filter(Exists(pending_item)).values("pk"),
            )
        )
        .filter(~Exists(active_attempt))
    )
    # Claim may resume PAUSED batches after the owner becomes eligible again.
    owners = list(batches.values_list("owner_id", flat=True).distinct()[:101])
    if len(owners) > 100:
        raise ValueError("import owner observation exceeded bound")
    eligible = [owner for owner in owners if _owner_is_eligible(owner)]
    eligible_batches = batches.filter(owner_id__in=eligible)
    ready_at = eligible_batches.filter(
        Q(manifest_complete=False)
        | Q(items__status="pending", items__download_attempts__lt=MAX_OPERATION_ATTEMPTS)
    ).aggregate(oldest=Min("last_activity_at"))["oldest"]
    expired_at = ImportAttempt.objects.filter(
        batch__in=eligible_batches,
        batch__manifest_complete=True,
        kind="file",
        status="active",
        lease_expires_at__lte=now,
        item__status__in=("claimed", "uploading"),
        item__download_attempts__lt=MAX_OPERATION_ATTEMPTS,
    ).aggregate(oldest=Min("lease_expires_at"))["oldest"]
    oldest = min((value for value in (ready_at, expired_at) if value is not None), default=None)
    success = ImportAttempt.objects.filter(status="succeeded").aggregate(last=Max("terminal_at"))[
        "last"
    ]
    return _sample(eligible_batches.count(), oldest, success, now)


def _commerce(now: datetime) -> QueueSample:
    email = (
        EmailDelivery.objects.filter(access_grant__revoked_at__isnull=True)
        .filter(
            Q(state__in=("pending", "retry_wait"), next_attempt_at__lte=now)
            | Q(state="processing", lease_expires_at__lte=now)
        )
        .aggregate(
            count=Count("pk"),
            oldest=Min("next_attempt_at", filter=Q(state__in=("pending", "retry_wait"))),
            expired=Min("lease_expires_at", filter=Q(state="processing")),
        )
    )
    payment = (
        PaymentAttempt.objects.filter(status="pending", adapter_key=TBANK_ADAPTER_KEY)
        .filter(
            Q(reconciliation_state="pending", reconciliation_next_attempt_at__lte=now)
            | Q(reconciliation_state="processing", reconciliation_lease_expires_at__lte=now)
        )
        .aggregate(
            count=Count("pk"),
            oldest=Min("reconciliation_next_attempt_at", filter=Q(reconciliation_state="pending")),
            expired=Min(
                "reconciliation_lease_expires_at", filter=Q(reconciliation_state="processing")
            ),
        )
    )
    successes = (
        EmailDeliveryAttempt.objects.filter(outcome="succeeded").aggregate(
            last=Max("attempted_at")
        )["last"],
        PaymentAttempt.objects.filter(status="succeeded").aggregate(last=Max("terminal_at"))[
            "last"
        ],
    )
    oldest = min(
        (value for value in (*email.values(), *payment.values()) if isinstance(value, datetime)),
        default=None,
    )
    success = max((value for value in successes if value), default=None)
    return _sample(email["count"] + payment["count"], oldest, success, now)


def collect_queue_samples() -> dict[str, QueueSample]:
    """A failed queue omits only its own series; no failure becomes a healthy zero."""
    now = timezone.now()
    samples: dict[str, QueueSample] = {}
    for name, collector in (
        ("gallery-search", _gallery),
        ("selfie", _selfie),
        ("photo", _photo),
        ("import", _import),
        ("commerce", _commerce),
    ):
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('statement_timeout')")
                    previous_timeout = cursor.fetchone()[0]
                    cursor.execute("SET LOCAL statement_timeout = 2000")
                sample = collector(now)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)", [previous_timeout]
                    )
            if sample is not None:
                samples[name] = sample
        except (DatabaseError, ValueError):
            continue
    return samples
