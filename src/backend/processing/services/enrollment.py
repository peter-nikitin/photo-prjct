from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    REPORT_JSON_MAX_BYTES,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingJob,
)

CONTRACT_VERSION = 1
PROCESSOR_VERSION = 1
CAPTURE_METADATA_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 20,
    "report_max_bytes": REPORT_JSON_MAX_BYTES,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    # Immutable processor semantics and worker bounds.  A changed value changes the normalized
    # run configuration and must be paired with a new processor version when it changes output.
    "capture_metadata": {
        "date_field_precedence": ["DateTimeOriginal", "DateTimeDigitized", "DateTime"],
        "normalization": "utc_assume_utc_if_missing",
    },
    "worker": {
        "api_response_max_bytes": 16_384,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": 100_000_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 8_192,
    },
}
DEFAULT_RECONCILIATION_LIMIT = 100
MAX_RECONCILIATION_LIMIT = 1_000


def request_capture_metadata(
    photo: Photo, *, verified_source_etag: str | None = None
) -> PhotoProcessingState:
    """Queue the first compatible capture-metadata job exactly once for an eligible photo."""
    with transaction.atomic():
        if not _is_eligible(photo):
            state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
                photo=photo,
                processor_type=CAPTURE_METADATA_PROCESSOR,
                defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
            )
            return state

        configuration = CAPTURE_METADATA_CONFIGURATION
        configuration_hash = _configuration_hash(configuration)
        input_fingerprint = _input_fingerprint(
            photo,
            verified_source_etag=verified_source_etag,
        )
        # Use the same run -> state ordering as claims to avoid a seal/enroll deadlock.
        run = _locked_collecting_run(
            event=Event.objects.select_for_update().get(pk=photo.event_id),
            configuration=configuration,
            configuration_hash=configuration_hash,
        )
        state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
            photo=photo,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
        if state.current_job_id is not None:
            return state
        job, _ = ProcessingJob.objects.get_or_create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=PROCESSOR_VERSION,
            configuration_hash=configuration_hash,
            defaults={
                "configuration": configuration,
                "input_fingerprint": input_fingerprint,
                "status": ProcessingJob.Status.QUEUED,
            },
        )

        now = timezone.now()
        state.status = PhotoProcessingState.Status.QUEUED
        state.current_run = run
        state.current_job = job
        state.queued_at = now
        state.save(
            update_fields=["status", "current_run", "current_job", "queued_at", "updated_at"]
        )
        return state


def reconcile_capture_metadata(
    *, limit: int = DEFAULT_RECONCILIATION_LIMIT
) -> list[PhotoProcessingState]:
    """Enroll one bounded, idempotent batch of eligible photos left without a current job."""
    if not 1 <= limit <= MAX_RECONCILIATION_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RECONCILIATION_LIMIT}")

    photo_ids = list(
        Photo.objects.filter(
            original_key__isnull=False,
            original_key__gt="",
            original_size__isnull=False,
            original_content_type="image/jpeg",
            processing_states__processor_type=CAPTURE_METADATA_PROCESSOR,
            processing_states__status=PhotoProcessingState.Status.NOT_REQUESTED,
            processing_states__current_job__isnull=True,
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    return [request_capture_metadata(Photo.objects.get(pk=photo_id)) for photo_id in photo_ids]


def _is_eligible(photo: Photo) -> bool:
    return bool(
        photo.original_key
        and photo.original_size is not None
        and photo.original_content_type == "image/jpeg"
    )


def _input_fingerprint(
    photo: Photo, *, verified_source_etag: str | None
) -> dict[str, int | str | None]:
    if verified_source_etag is None:
        try:
            verified_source_etag = photo.upload_item.verified_source_etag
        except ObjectDoesNotExist:
            pass
    if not verified_source_etag:
        verified_source_etag = None
    return {
        "original_key": photo.original_key,
        "original_size": photo.original_size,
        "original_content_type": photo.original_content_type,
        "verified_source_etag": verified_source_etag,
        "version_evidence": "verified_source_etag" if verified_source_etag else "unavailable",
    }


def _configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json.dumps(configuration, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _locked_collecting_run(*, event, configuration: dict[str, object], configuration_hash: str):
    """Return a collecting run under lock so a concurrent claim seals an exact cohort."""
    query = EventProcessingRun.objects.select_for_update().filter(
        event=event,
        contract_version=CONTRACT_VERSION,
        processor_type=CAPTURE_METADATA_PROCESSOR,
        processor_version=PROCESSOR_VERSION,
        configuration_hash=configuration_hash,
        status=EventProcessingRun.Status.COLLECTING,
    )
    configured_maximum = configuration["max_cohort_size"]
    if not isinstance(configured_maximum, int):
        raise ValueError("max_cohort_size must be an integer")
    for run in query:
        if run.jobs.count() < configured_maximum:
            return run
    return EventProcessingRun.objects.create(
        event=event,
        contract_version=CONTRACT_VERSION,
        processor_type=CAPTURE_METADATA_PROCESSOR,
        processor_version=PROCESSOR_VERSION,
        configuration=configuration,
        configuration_hash=configuration_hash,
        status=EventProcessingRun.Status.COLLECTING,
    )
