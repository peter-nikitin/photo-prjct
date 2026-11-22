"""Read-only ready-work observations from the same durable predicates as claims."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from commerce.models import EmailDelivery, EmailDeliveryAttempt, PaymentAttempt
from commerce.tbank_gateway import TBANK_ADAPTER_KEY
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Exists, Max, Min, OuterRef, Q, QuerySet
from django.utils import timezone
from ingestion.models import ImportAttempt, ImportBatch, ImportItem
from ingestion.services.imports import MAX_OPERATION_ATTEMPTS, _owner_is_eligible
from picflow.models import PhotoArchiveJob
from processing.models import EventProcessingRun, PhotoProcessingState, ProcessingJob
from processing.services.worker_pool_state import BULK_IDENTITIES
from selfie_search.models import FaceSearchTask, SelfieSearch, SelfieSearchJob

from config.queue_catalog import QUEUES

type QueueSample = tuple[int, float, float, float]


def _stamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0.0


def _sample(
    count: int, oldest: datetime | None, success: datetime | None, now: datetime
) -> QueueSample:
    age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
    return count, age, _stamp(success), time.time()


def _matching(now: datetime, *, source: str, processor: str) -> QueueSample:
    ready = FaceSearchTask.objects.filter(
        source=source, status=FaceSearchTask.Status.QUEUED
    ).filter(Q(available_at__lte=now) | Q(expires_at__lte=now))
    values = ready.aggregate(count=Count("pk"), oldest=Min("available_at"))
    success = SelfieSearch.objects.filter(
        configuration__processor=processor, status=SelfieSearch.Status.READY
    ).aggregate(last=Max("terminal_at"))["last"]
    return _sample(values["count"], values["oldest"], success, now)


def _gallery(now: datetime) -> QueueSample:
    return _matching(
        now, source=str(FaceSearchTask.Source.GALLERY), processor="gallery_photo_query"
    )


def _selfie_matching(now: datetime) -> QueueSample:
    return _matching(now, source=str(FaceSearchTask.Source.SELFIE), processor="selfie_query")


def _selfie(now: datetime) -> QueueSample | None:
    if not settings.PHOTO_PROCESSING_ENABLED:
        return _sample(0, None, None, now)
    ready = _ready_selfie(now)
    values = ready.aggregate(count=Count("pk"), oldest=Min("available_at"))
    success = SelfieSearchJob.objects.filter(status="succeeded").aggregate(
        last=Max("completed_at")
    )["last"]
    return _sample(values["count"], values["oldest"], success, now)


def _ready_selfie(now: datetime) -> QuerySet[SelfieSearchJob]:
    return SelfieSearchJob.objects.filter(
        status__in=("queued", "retry_wait"),
        available_at__lte=now,
        search__status="queued",
        search__stage=SelfieSearch.Stage.RECOGNITION,
        search__temporary_object_key__gt="",
    )


def _photo_jobs() -> QuerySet[ProcessingJob]:
    identities = Q()
    for contract, processor, version in BULK_IDENTITIES:
        identities |= Q(
            contract_version=contract, processor_type=processor, processor_version=version
        )
    return ProcessingJob.objects.filter(identities)


def _ready_photo(jobs: QuerySet[ProcessingJob], now: datetime) -> QuerySet[ProcessingJob]:
    current = PhotoProcessingState.objects.filter(
        current_job_id=OuterRef("pk"),
        photo_id=OuterRef("photo_id"),
        processor_type=OuterRef("processor_type"),
    )
    return jobs.filter(
        Exists(current),
        status__in=("queued", "retry_wait"),
        available_at__lte=now,
        run__status__in=(
            EventProcessingRun.Status.COLLECTING,
            EventProcessingRun.Status.SEALED,
        ),
    )


def _photo(now: datetime) -> QueueSample:
    if not settings.PHOTO_PROCESSING_ENABLED:
        return _sample(0, None, None, now)
    jobs = _photo_jobs()
    ready = _ready_photo(jobs, now)
    values = ready.aggregate(count=Count("pk"), oldest=Min("available_at"))
    success = (
        jobs.filter(status="succeeded", completed_at__isnull=False)
        .order_by("-completed_at")
        .values_list("completed_at", flat=True)
        .first()
    )
    return _sample(values["count"], values["oldest"], success, now)


def _eligible_imports(now: datetime) -> QuerySet[ImportBatch]:
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
    return batches.filter(owner_id__in=eligible)


def _import(now: datetime) -> QueueSample:
    eligible_batches = _eligible_imports(now)
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


def _ready_email(now: datetime) -> QuerySet[EmailDelivery]:
    return EmailDelivery.objects.filter(access_grant__revoked_at__isnull=True).filter(
        Q(state__in=("pending", "retry_wait"), next_attempt_at__lte=now)
        | Q(state="processing", lease_expires_at__lte=now)
    )


def _ready_payment(now: datetime) -> QuerySet[PaymentAttempt]:
    return PaymentAttempt.objects.filter(status="pending", adapter_key=TBANK_ADAPTER_KEY).filter(
        Q(reconciliation_state="pending", reconciliation_next_attempt_at__lte=now)
        | Q(reconciliation_state="processing", reconciliation_lease_expires_at__lte=now)
    )


def _commerce(now: datetime) -> QueueSample:
    email = _ready_email(now).aggregate(
        count=Count("pk"),
        oldest=Min("next_attempt_at", filter=Q(state__in=("pending", "retry_wait"))),
        expired=Min("lease_expires_at", filter=Q(state="processing")),
    )
    payment = _ready_payment(now).aggregate(
        count=Count("pk"),
        oldest=Min("reconciliation_next_attempt_at", filter=Q(reconciliation_state="pending")),
        expired=Min("reconciliation_lease_expires_at", filter=Q(reconciliation_state="processing")),
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


def _archive(now: datetime) -> QueueSample:
    ready = PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.QUEUED)
    values = ready.aggregate(count=Count("pk"), oldest=Min("updated_at"))
    success = (
        PhotoArchiveJob.objects.filter(status=PhotoArchiveJob.Status.READY)
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    return _sample(values["count"], values["oldest"], success, now)


def collect_queue_samples() -> dict[str, QueueSample]:
    """A failed queue omits only its own series; no failure becomes a healthy zero."""
    now = timezone.now()
    samples: dict[str, QueueSample] = {}
    for name, collector in (
        ("gallery-search", _gallery),
        ("selfie", _selfie),
        ("selfie_face_search", _selfie_matching),
        ("photo", _photo),
        ("import", _import),
        ("commerce", _commerce),
        ("photo_archive", _archive),
    ):
        try:
            with _observation_timeout():
                sample = collector(now)
            if sample is not None:
                samples[name] = sample
        except (DatabaseError, ValueError):
            continue
    return samples


@contextmanager
def _observation_timeout(timeout_ms: int = 2000) -> Iterator[None]:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('statement_timeout')")
            previous_timeout = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", [str(timeout_ms)])
        yield
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", [previous_timeout])


type ReadySample = tuple[int, float | None, float]


def _ready_age(rows: QuerySet, enqueue_field: str, now: datetime) -> ReadySample:
    values = rows.aggregate(
        count=Count("pk"), known=Count(enqueue_field), oldest=Min(enqueue_field)
    )
    oldest = values["oldest"]
    count = values["count"]
    age = (
        None
        if values["known"] != count
        else max(0.0, (now - oldest).total_seconds())
        if oldest is not None
        else 0.0
    )
    return count, age, time.time()


def _panel_ready(queue: str, now: datetime) -> ReadySample:
    if queue == "disk_import":
        return _ready_age(_eligible_imports(now), "queue_enqueued_at", now)
    if queue == "photo_archive":
        return _ready_age(PhotoArchiveJob.objects.filter(status="queued"), "queue_enqueued_at", now)
    if queue == "commerce_email":
        return _ready_age(_ready_email(now), "queue_enqueued_at", now)
    if queue == "commerce_payment":
        return _ready_age(_ready_payment(now), "queue_enqueued_at", now)
    if queue in ("gallery_matching", "selfie_matching"):
        source = queue.removesuffix("_matching")
        ready = FaceSearchTask.objects.filter(source=source, status="queued").filter(
            Q(available_at__lte=now) | Q(expires_at__lte=now)
        )
        return _ready_age(ready, "search__matching_enqueued_at", now)
    if not settings.PHOTO_PROCESSING_ENABLED:
        return 0, 0.0, time.time()
    if queue == "selfie_recognition":
        return _ready_age(_ready_selfie(now), "created_at", now)
    processor = queue.removeprefix("photo_")
    return _ready_age(
        _ready_photo(_photo_jobs(), now).filter(processor_type=processor), "created_at", now
    )


def collect_ready_samples() -> dict[str, ReadySample]:
    now = timezone.now()
    samples: dict[str, ReadySample] = {}
    for queue in QUEUES:
        try:
            with _observation_timeout(100):
                sample = _panel_ready(queue.key, now)
        except (DatabaseError, ValueError):
            continue
        samples[queue.key] = sample
    return samples
