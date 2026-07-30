from __future__ import annotations

import hashlib
import json
from typing import cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    REPORT_JSON_MAX_BYTES,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingJob,
)

CONTRACT_VERSION = 1
PROCESSOR_VERSION = 1
FACE_EMBEDDING_PROCESSOR_VERSION = 1
PREVIEW_CONTRACT_VERSION = 2
GENERATE_PREVIEW_PROCESSOR_VERSION = 1
PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION = 2

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

FACE_EMBEDDING_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": REPORT_JSON_MAX_BYTES,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "face_embedding": {
        "model": "sface",
        "min_face_px": 32,
        "max_faces_per_photo": 32,
        "normalize_embeddings": True,
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

GENERATE_PREVIEW_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": REPORT_JSON_MAX_BYTES,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "generate_preview": {
        "variant": "preview-small-v1",
        "output_format": "jpeg",
        "max_long_edge": 1600,
        "jpeg_quality": 85,
        "color_space": "srgb",
        "upscale": False,
        "apply_exif_orientation": True,
        "strip_metadata": True,
        "watermark": "none",
        "max_output_bytes": 10 * 1024 * 1024,
        "max_output_width": 1600,
        "max_output_height": 1600,
        "checksum_algorithm": "sha256",
    },
    "worker": {
        "api_response_max_bytes": 16_384,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": 24_000_000,
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
    return request_processor(
        photo=photo,
        processor_type=CAPTURE_METADATA_PROCESSOR,
        contract_version=CONTRACT_VERSION,
        processor_version=PROCESSOR_VERSION,
        configuration=CAPTURE_METADATA_CONFIGURATION,
        verified_source_etag=verified_source_etag,
        enabled=True,
    )


def request_face_embedding_enqueue(
    photo: Photo, *, verified_source_etag: str | None = None
) -> PhotoProcessingState:
    """Queue a face-embedding job if the feature flag is enabled."""
    preview = _accepted_preview(photo)
    if photo.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_V1:
        return request_processor(
            photo=photo,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            contract_version=PREVIEW_CONTRACT_VERSION,
            processor_version=PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            input_fingerprint=_derivative_fingerprint(preview) if preview is not None else None,
            enabled=bool(getattr(settings, "PHOTO_PROCESSING_FACE_ENABLED", False))
            and preview is not None,
        )
    return request_processor(
        photo=photo,
        processor_type=FACE_EMBEDDING_PROCESSOR,
        contract_version=CONTRACT_VERSION,
        processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
        configuration=FACE_EMBEDDING_CONFIGURATION,
        verified_source_etag=verified_source_etag,
        enabled=bool(getattr(settings, "PHOTO_PROCESSING_FACE_ENABLED", False)),
    )


def request_generate_preview(
    photo: Photo,
    *,
    pixel_width: int,
    pixel_height: int,
    verified_source_etag: str | None = None,
) -> PhotoProcessingState:
    """Queue the original-backed preview only for an explicitly preview-first photo."""
    return request_processor(
        photo=photo,
        processor_type=GENERATE_PREVIEW_PROCESSOR,
        contract_version=PREVIEW_CONTRACT_VERSION,
        processor_version=GENERATE_PREVIEW_PROCESSOR_VERSION,
        configuration=GENERATE_PREVIEW_CONFIGURATION,
        input_fingerprint={
            "object_key": photo.original_key,
            "object_size": photo.original_size,
            "object_content_type": photo.original_content_type,
            "object_etag": verified_source_etag,
            "media_kind": "original",
            "pixel_width": pixel_width,
            "pixel_height": pixel_height,
        },
        enabled=(
            photo.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_V1
            and photo.gallery_media_policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        ),
    )


def request_processor(
    photo: Photo,
    *,
    processor_type: str,
    contract_version: int,
    processor_version: int,
    configuration: dict[str, object],
    verified_source_etag: str | None = None,
    input_fingerprint: dict[str, int | str | None] | None = None,
    enabled: bool = True,
) -> PhotoProcessingState:
    """Queue the first compatible job exactly once for an eligible photo."""
    with transaction.atomic():
        if not enabled or not _is_eligible(photo):
            state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
                photo=photo,
                processor_type=processor_type,
                defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
            )
            return state

        configuration_hash = _configuration_hash(configuration)
        if input_fingerprint is None:
            input_fingerprint = _input_fingerprint(
                photo,
                verified_source_etag=verified_source_etag,
            )
        # Use the same run -> state ordering as claims to avoid a seal/enroll deadlock.
        run = _locked_collecting_run(
            event=Event.objects.select_for_update().get(pk=photo.event_id),
            configuration=configuration,
            configuration_hash=configuration_hash,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
        )
        state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
            photo=photo,
            processor_type=processor_type,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
        if state.current_job_id is not None:
            return state
        job, _ = ProcessingJob.objects.get_or_create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
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
    return _reconcile(
        processor_type=CAPTURE_METADATA_PROCESSOR, limit=limit, processor_enabled=True
    )


def reconcile_face_embedding(
    *, limit: int = DEFAULT_RECONCILIATION_LIMIT
) -> list[PhotoProcessingState]:
    """Enroll one bounded, idempotent batch of eligible photos for face embeddings."""
    if not bool(getattr(settings, "PHOTO_PROCESSING_FACE_ENABLED", False)):
        return []
    if not 1 <= limit <= MAX_RECONCILIATION_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RECONCILIATION_LIMIT}")
    reconciled: list[PhotoProcessingState] = []
    for photo in Photo.objects.order_by("pk"):
        if photo.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_V1:
            if _accepted_preview(photo) is None:
                continue
        elif not _is_eligible(photo):
            continue
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
        if state.status != PhotoProcessingState.Status.NOT_REQUESTED or state.current_job_id:
            continue
        reconciled.append(request_face_embedding_enqueue(photo))
        if len(reconciled) >= limit:
            break
    return reconciled


def _reconcile(
    *, processor_type: str, limit: int, processor_enabled: bool
) -> list[PhotoProcessingState]:
    if not processor_enabled:
        return []
    if not 1 <= limit <= MAX_RECONCILIATION_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RECONCILIATION_LIMIT}")

    photo_ids = _reconcilable_photo_ids(processor_type=processor_type, limit=limit)
    config = _reconcile_config(processor_type)
    return [
        request_processor(
            Photo.objects.get(pk=photo_id),
            processor_type=processor_type,
            contract_version=cast(int, config["contract_version"]),
            processor_version=cast(int, config["processor_version"]),
            configuration=cast(dict[str, object], config["configuration"]),
            verified_source_etag=cast(str | None, config["verified_source_etag"]),
        )
        for photo_id in photo_ids
    ]


def _reconcilable_photo_ids(*, processor_type: str, limit: int) -> list[str]:
    eligible_photos = Photo.objects.filter(
        original_key__isnull=False,
        original_key__gt="",
        original_size__isnull=False,
        original_content_type="image/jpeg",
    ).order_by("pk")

    if processor_type == CAPTURE_METADATA_PROCESSOR:
        return list(
            eligible_photos.filter(
                processing_states__processor_type=processor_type,
                processing_states__status=PhotoProcessingState.Status.NOT_REQUESTED,
                processing_states__current_job__isnull=True,
            ).values_list("pk", flat=True)[:limit]
        )

    if processor_type == FACE_EMBEDDING_PROCESSOR:
        photo_ids: list[str] = []
        for photo in eligible_photos:
            state, _ = PhotoProcessingState.objects.get_or_create(
                photo=photo,
                processor_type=processor_type,
                defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
            )
            if (
                state.status == PhotoProcessingState.Status.NOT_REQUESTED
                and state.current_job_id is None
            ):
                photo_ids.append(photo.pk)
            if len(photo_ids) >= limit:
                break
        return photo_ids

    return []


def _reconcile_config(processor_type: str) -> dict[str, object]:
    if processor_type == CAPTURE_METADATA_PROCESSOR:
        return {
            "contract_version": CONTRACT_VERSION,
            "processor_version": PROCESSOR_VERSION,
            "configuration": CAPTURE_METADATA_CONFIGURATION,
            "verified_source_etag": None,
        }
    if processor_type == FACE_EMBEDDING_PROCESSOR:
        return {
            "contract_version": CONTRACT_VERSION,
            "processor_version": FACE_EMBEDDING_PROCESSOR_VERSION,
            "configuration": FACE_EMBEDDING_CONFIGURATION,
            "verified_source_etag": None,
        }
    raise ValueError(f"unsupported processor_type: {processor_type}")


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


def _accepted_preview(photo: Photo) -> PhotoDerivative | None:
    preview = PhotoDerivative.objects.filter(photo=photo, variant="preview-small-v1").first()
    if preview is None:
        return None
    try:
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=GENERATE_PREVIEW_PROCESSOR
        )
    except PhotoProcessingState.DoesNotExist:
        return None
    if (
        state.status != PhotoProcessingState.Status.SUCCEEDED
        or state.accepted_attempt_id != preview.accepted_attempt_id
    ):
        return None
    return preview


def _derivative_fingerprint(
    derivative: PhotoDerivative,
) -> dict[str, int | str | None]:
    return {
        "object_key": derivative.final_key,
        "object_size": derivative.byte_size,
        "object_content_type": derivative.content_type,
        "object_etag": None,
        "media_kind": derivative.variant,
        "pixel_width": derivative.width,
        "pixel_height": derivative.height,
    }


def _configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json.dumps(configuration, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _locked_collecting_run(
    *,
    event,
    configuration: dict[str, object],
    configuration_hash: str,
    contract_version: int,
    processor_type: str,
    processor_version: int,
):
    """Return a collecting run under lock so a concurrent claim seals an exact cohort."""
    query = EventProcessingRun.objects.select_for_update().filter(
        event=event,
        contract_version=contract_version,
        processor_type=processor_type,
        processor_version=processor_version,
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
        contract_version=contract_version,
        processor_type=processor_type,
        processor_version=processor_version,
        configuration=configuration,
        configuration_hash=configuration_hash,
        status=EventProcessingRun.Status.COLLECTING,
    )
