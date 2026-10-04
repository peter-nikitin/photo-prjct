from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import TypedDict, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from picflow.models import Event, Photo

from processing.contracts import BIB_RECOGNITION_CONTRACT, FACE_EMBEDDING_CONTRACT
from processing.models import (
    BIB_RECOGNITION_PROCESSOR,
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    REPORT_JSON_MAX_BYTES,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingJob,
)
from processing.services.bibs import bib_configuration
from processing.services.jobs import transition_capture_time_projection

CONTRACT_VERSION = 1
CAPTURE_METADATA_PROCESSOR_VERSION = 2
PREVIEW_CONTRACT_VERSION = 2
GENERATE_PREVIEW_PROCESSOR_VERSION = 1
GENERATE_WATERMARKED_PREVIEW_PROCESSOR_VERSION = 1
QUALITY_FACE_CONTRACT_VERSION = FACE_EMBEDDING_CONTRACT.contract_version
QUALITY_FACE_PROCESSOR_VERSION = FACE_EMBEDDING_CONTRACT.processor_version

_CAPTURE_METADATA_CONFIGURATION_BASE: dict[str, object] = {
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


def capture_metadata_configuration(event_timezone: str | None) -> dict[str, object]:
    """Build the complete immutable capture-metadata v2 configuration for one event."""
    if not isinstance(event_timezone, str):
        raise ValueError("event timezone must be a valid IANA timezone")
    try:
        ZoneInfo(event_timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("event timezone must be a valid IANA timezone") from error
    configuration = deepcopy(_CAPTURE_METADATA_CONFIGURATION_BASE)
    configuration["capture_metadata"] = {
        "date_field_precedence": ["DateTimeOriginal", "DateTimeDigitized", "DateTime"],
        "normalization": "utc_explicit_offset_or_event_timezone",
        "event_timezone": event_timezone,
    }
    return configuration


FACE_EMBEDDING_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": 262144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "face_embedding": {
        "model": "adaface-ir18-webface4m",
        "embedding_dimensions": 512,
        "max_faces": 32,
        "detection_threshold": 0.5,
        "normalize_embeddings": True,
        "quality": {
            "algorithm_version": "normalized-laplacian-v1",
            "crop_size": 112,
            "minimum_face_px": 32,
            "severe_blur_threshold": 25.0,
            "borderline_blur_threshold": 50.0,
            "minimum_relative_area": 0.0009,
            "minimum_confidence": 0.82,
        },
    },
    "worker": {
        "api_response_max_bytes": 393216,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 52428800,
        "max_pixels": 100000000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 393216,
    },
    "scrfd": {
        "input_size": [640, 640],
        "model": "scrfd-10g-kps",
        "model_artifact_sha256": (
            "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"
        ),
        "nms_threshold": 0.4,
    },
    "adaface": {
        "alignment": "scrfd-five-landmark-112x112",
        "input_normalization": "rgb-value-over-255-minus-0.5-over-0.5",
        "model_artifact_sha256": (
            "3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b"
        ),
        "model_revision": "0dd53f188fa27968b0a1326970ebf4aeb37ce2ca",
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

GENERATE_WATERMARKED_PREVIEW_CONFIGURATION: dict[str, object] = {
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
    "generate_watermarked_preview": {
        "variant": "preview-watermarked-v1",
        "input_variant": "preview-small-v1",
        "output_format": "jpeg",
        "jpeg_quality": 85,
        "color_space": "srgb",
        "strip_metadata": True,
        "checksum_algorithm": "sha256",
        "landscape_asset_sha256": (
            "adab8dcc93c744a79f8a33dc236f3da2e586b21a92070d902f4599dd27c161fa"
        ),
        "portrait_asset_sha256": (
            "d28f386783bed634eb55e7691217e434c697035108c24b7b8b066cb9af27b70a"
        ),
    },
    "worker": {
        "api_response_max_bytes": 16_384,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 10_485_760,
        "max_pixels": 2_560_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 8_192,
    },
}

DEFAULT_RECONCILIATION_LIMIT = 100
MAX_RECONCILIATION_LIMIT = 1_000


class _ReconciliationProcessorConfig(TypedDict):
    contract_version: int
    processor_version: int
    configuration: dict[str, object]
    verified_source_etag: str | None


@dataclass(frozen=True)
class CaptureTimeReprocessingEnrollment:
    photo_count: int
    created_job_count: int
    existing_job_count: int
    run_count: int


@dataclass(frozen=True)
class CaptureTimeReprocessingTarget:
    event_id: int
    event_name: str
    timezone_name: str
    photo_count: int
    configuration: dict[str, object]


def validate_capture_time_reprocessing_enrollment(
    event: Event,
    *,
    target: CaptureTimeReprocessingTarget,
) -> dict[str, object]:
    """Reject mixed or duplicate v2 capture-time evidence without writing any rows."""
    configuration, _ = _validate_capture_time_reprocessing_target(event, target=target)
    configuration_hash = _configuration_hash(configuration)
    jobs = list(
        ProcessingJob.objects.select_related("run")
        .filter(
            event=event,
            contract_version=CONTRACT_VERSION,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
        )
        .order_by("photo_id", "created_at", "id")
    )
    seen_photo_ids: set[str] = set()
    for job in jobs:
        if job.photo_id in seen_photo_ids:
            raise ValueError("event has duplicate capture-metadata version-2 jobs")
        seen_photo_ids.add(job.photo_id)
        if not _is_exact_capture_time_reprocessing_job(
            job,
            configuration=configuration,
            configuration_hash=configuration_hash,
        ):
            raise ValueError("event has an unexpected capture-metadata version-2 configuration")
    return configuration


def enroll_event_capture_time_reprocessing(
    event: Event,
    *,
    target: CaptureTimeReprocessingTarget,
) -> CaptureTimeReprocessingEnrollment:
    """Enroll one exact event cohort, rotating only its mutable capture-time state pointers."""
    configuration = validate_capture_time_reprocessing_enrollment(event, target=target)
    configuration_hash = _configuration_hash(configuration)
    max_cohort_size = configuration["max_cohort_size"]
    if not isinstance(max_cohort_size, int) or max_cohort_size < 1:
        raise ValueError("capture-metadata configuration has an invalid cohort size")

    discovered_photo_ids = list(
        Photo.objects.filter(event=event).order_by("pk").values_list("pk", flat=True)
    )
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        existing_job_run_ids = ProcessingJob.objects.filter(
            event=locked_event,
            contract_version=CONTRACT_VERSION,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
        ).values_list("run_id", flat=True)
        collecting_runs = list(
            EventProcessingRun.objects.select_for_update()
            .filter(
                Q(pk__in=existing_job_run_ids)
                | Q(
                    event=locked_event,
                    contract_version=CONTRACT_VERSION,
                    processor_type=CAPTURE_METADATA_PROCESSOR,
                    processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
                    configuration_hash=configuration_hash,
                    status=EventProcessingRun.Status.COLLECTING,
                )
            )
            .order_by("id")
        )
        existing_jobs = list(
            ProcessingJob.objects.select_for_update()
            .select_related("run")
            .filter(
                event=locked_event,
                contract_version=CONTRACT_VERSION,
                processor_type=CAPTURE_METADATA_PROCESSOR,
                processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            )
            .order_by("photo_id", "created_at", "id")
        )
        jobs_by_photo_id: dict[str, ProcessingJob] = {}
        for job in existing_jobs:
            if job.photo_id in jobs_by_photo_id:
                raise ValueError("event has duplicate capture-metadata version-2 jobs")
            if not _is_exact_capture_time_reprocessing_job(
                job,
                configuration=configuration,
                configuration_hash=configuration_hash,
            ):
                raise ValueError("event has an unexpected capture-metadata version-2 configuration")
            jobs_by_photo_id[job.photo_id] = job

        assigned_runs: dict[str, EventProcessingRun] = {}
        available_runs = [
            [run, max_cohort_size - ProcessingJob.objects.filter(run=run).count()]
            for run in collecting_runs
            if (
                run.contract_version == CONTRACT_VERSION
                and run.processor_type == CAPTURE_METADATA_PROCESSOR
                and run.processor_version == CAPTURE_METADATA_PROCESSOR_VERSION
                and run.configuration == configuration
                and run.configuration_hash == configuration_hash
                and run.status == EventProcessingRun.Status.COLLECTING
            )
        ]
        for photo_id in discovered_photo_ids:
            if photo_id in jobs_by_photo_id:
                continue
            available = next((item for item in available_runs if item[1] > 0), None)
            if available is None:
                available = [
                    EventProcessingRun.objects.create(
                        event=locked_event,
                        contract_version=CONTRACT_VERSION,
                        processor_type=CAPTURE_METADATA_PROCESSOR,
                        processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
                        configuration=configuration,
                        configuration_hash=configuration_hash,
                        status=EventProcessingRun.Status.COLLECTING,
                    ),
                    max_cohort_size,
                ]
                available_runs.append(available)
            assigned_runs[photo_id] = available[0]
            available[1] -= 1

        photos = list(Photo.objects.select_for_update().filter(event=locked_event).order_by("pk"))
        locked_configuration, locked_photo_count = _validate_capture_time_reprocessing_target(
            locked_event,
            target=target,
            photo_count=len(photos),
        )
        if locked_configuration != configuration:
            raise ValueError("event capture-metadata configuration changed during enrollment")
        created_job_count = 0
        now = timezone.now()
        for photo in photos:
            job = jobs_by_photo_id.get(photo.pk)
            if job is None:
                job = ProcessingJob.objects.create(
                    event=locked_event,
                    run=assigned_runs[photo.pk],
                    photo=photo,
                    contract_version=CONTRACT_VERSION,
                    processor_type=CAPTURE_METADATA_PROCESSOR,
                    processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
                    configuration=configuration,
                    configuration_hash=configuration_hash,
                    input_fingerprint=_input_fingerprint(photo, verified_source_etag=None),
                    status=ProcessingJob.Status.QUEUED,
                )
                jobs_by_photo_id[photo.pk] = job
                created_job_count += 1

            state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
                photo=photo,
                processor_type=CAPTURE_METADATA_PROCESSOR,
                defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
            )
            if state.current_job_id != job.id:
                if job.status != ProcessingJob.Status.QUEUED:
                    raise ValueError("existing capture-metadata version-2 job is not current")
                state.status = PhotoProcessingState.Status.QUEUED
                state.current_run = job.run
                state.current_job = job
                state.current_attempt = None
                state.accepted_attempt = None
                state.next_attempt_at = None
                state.queued_at = now
                state.processing_at = None
                state.succeeded_at = None
                state.failed_at = None
                state.cancelled_at = None
                state.save(
                    update_fields=[
                        "status",
                        "current_run",
                        "current_job",
                        "current_attempt",
                        "accepted_attempt",
                        "next_attempt_at",
                        "queued_at",
                        "processing_at",
                        "succeeded_at",
                        "failed_at",
                        "cancelled_at",
                        "updated_at",
                    ]
                )
                transition_capture_time_projection(photo=photo, state=state, accepted_attempt=None)

        return CaptureTimeReprocessingEnrollment(
            photo_count=locked_photo_count,
            created_job_count=created_job_count,
            existing_job_count=len(photos) - created_job_count,
            run_count=len({job.run_id for job in jobs_by_photo_id.values()}),
        )


def _validate_capture_time_reprocessing_target(
    event: Event,
    *,
    target: CaptureTimeReprocessingTarget,
    photo_count: int | None = None,
) -> tuple[dict[str, object], int]:
    if event.pk != target.event_id:
        raise ValueError("event does not match the approved reprocessing target")
    if event.name != target.event_name:
        raise ValueError("event name does not match the approved reprocessing target")
    if event.publication_status != Event.PublicationStatus.PUBLISHED:
        raise ValueError("event must be published")
    if event.timezone_name != target.timezone_name:
        raise ValueError("event timezone does not match the approved reprocessing target")
    if photo_count is None:
        photo_count = event.photos.count()
    if photo_count != target.photo_count:
        raise ValueError("event does not have the approved photo count")
    configuration = capture_metadata_configuration(event.timezone_name)
    if configuration != target.configuration:
        raise ValueError("event capture-metadata configuration does not match approval")
    return configuration, photo_count


def _is_exact_capture_time_reprocessing_job(
    job: ProcessingJob,
    *,
    configuration: dict[str, object],
    configuration_hash: str,
) -> bool:
    return bool(
        job.configuration == configuration
        and job.configuration_hash == configuration_hash
        and job.run.contract_version == CONTRACT_VERSION
        and job.run.processor_type == CAPTURE_METADATA_PROCESSOR
        and job.run.processor_version == CAPTURE_METADATA_PROCESSOR_VERSION
        and job.run.configuration == configuration
        and job.run.configuration_hash == configuration_hash
    )


def request_capture_metadata(
    photo: Photo, *, verified_source_etag: str | None = None
) -> PhotoProcessingState:
    """Queue the first compatible capture-metadata job exactly once for an eligible photo."""
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=photo.event_id)
        return request_processor(
            photo=photo,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            contract_version=CONTRACT_VERSION,
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            configuration=capture_metadata_configuration(event.timezone_name),
            verified_source_etag=verified_source_etag,
            enabled=True,
            event=event,
        )


def request_bib_recognition(
    photo: Photo, *, verified_source_etag: str | None = None
) -> PhotoProcessingState:
    """Queue bib work only from the photo's immutable policy and accepted publication."""
    return request_processor(
        photo=photo,
        processor_type=BIB_RECOGNITION_CONTRACT.processor_type,
        contract_version=BIB_RECOGNITION_CONTRACT.contract_version,
        processor_version=BIB_RECOGNITION_CONTRACT.processor_version,
        configuration=bib_configuration(),
        verified_source_etag=verified_source_etag,
        enabled=(
            photo.bib_processing_policy == Photo.BibProcessingPolicy.ORIGINAL_V1
            and _has_accepted_publication(photo)
        ),
    )


@transaction.atomic
def request_face_embedding_enqueue(
    photo: Photo, *, verified_source_etag: str | None = None
) -> PhotoProcessingState:
    """Queue face embedding for accepted preview evidence."""
    photo.event = Event.objects.select_for_update().get(pk=photo.event_id)
    preview = _accepted_preview(photo)
    from processing.services.face_quality import current_face_embedding_generation

    generation = current_face_embedding_generation()
    return request_processor(
        photo=photo,
        processor_type=FACE_EMBEDDING_PROCESSOR,
        contract_version=cast(int, generation["contract_version"]),
        processor_version=cast(int, generation["processor_version"]),
        configuration=cast(dict[str, object], generation["configuration"]),
        input_fingerprint=_derivative_fingerprint(preview) if preview is not None else None,
        enabled=preview is not None,
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
            (
                photo.processing_generation,
                photo.gallery_media_policy,
            )
            in {
                (
                    Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
                    Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
                ),
                (
                    Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
                    Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
                ),
            }
        ),
    )


def request_generate_watermarked_preview(
    photo: Photo,
    clean_preview: PhotoDerivative,
) -> PhotoProcessingState:
    """Queue watermark rendering only from this photo's accepted clean preview."""
    accepted_preview = _accepted_preview(photo)
    enabled = bool(
        photo.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        and photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        and accepted_preview is not None
        and accepted_preview.pk == clean_preview.pk
    )
    return request_processor(
        photo=photo,
        processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        contract_version=PREVIEW_CONTRACT_VERSION,
        processor_version=GENERATE_WATERMARKED_PREVIEW_PROCESSOR_VERSION,
        configuration=GENERATE_WATERMARKED_PREVIEW_CONFIGURATION,
        input_fingerprint=(_derivative_fingerprint(clean_preview) if enabled else None),
        enabled=enabled,
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
    event: Event | None = None,
    replace_terminal_generation: bool = False,
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
        # Use the same run -> state ordering as claims to avoid a seal/enroll deadlock.
        locked_event = event or Event.objects.select_for_update().get(pk=photo.event_id)
        run = _locked_collecting_run(
            event=locked_event,
            configuration=configuration,
            configuration_hash=configuration_hash,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
        )
        existing_job = (
            ProcessingJob.objects.select_for_update()
            .filter(
                event=locked_event,
                run=run,
                photo_id=photo.pk,
                contract_version=contract_version,
                processor_type=processor_type,
                processor_version=processor_version,
                configuration_hash=configuration_hash,
            )
            .first()
        )
        locked_photo = Photo.objects.select_for_update().get(pk=photo.pk)
        if input_fingerprint is None:
            input_fingerprint = _input_fingerprint(
                locked_photo,
                verified_source_etag=verified_source_etag,
            )
        state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
            photo=locked_photo,
            processor_type=processor_type,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
        replacing_terminal_generation = False
        if state.current_job_id is not None:
            current_job = state.current_job
            if (
                current_job.contract_version == contract_version
                and current_job.processor_version == processor_version
                and current_job.configuration_hash == configuration_hash
            ):
                if processor_type == CAPTURE_METADATA_PROCESSOR and state.status in {
                    PhotoProcessingState.Status.QUEUED,
                    PhotoProcessingState.Status.PROCESSING,
                    PhotoProcessingState.Status.RETRY_WAIT,
                }:
                    transition_capture_time_projection(
                        photo=locked_photo, state=state, accepted_attempt=None
                    )
                return state
            if not replace_terminal_generation:
                if processor_type == CAPTURE_METADATA_PROCESSOR and state.status in {
                    PhotoProcessingState.Status.QUEUED,
                    PhotoProcessingState.Status.PROCESSING,
                    PhotoProcessingState.Status.RETRY_WAIT,
                }:
                    transition_capture_time_projection(
                        photo=locked_photo, state=state, accepted_attempt=None
                    )
                return state
            if state.status in {
                PhotoProcessingState.Status.QUEUED,
                PhotoProcessingState.Status.PROCESSING,
                PhotoProcessingState.Status.RETRY_WAIT,
            }:
                raise ValueError("cannot replace active face-embedding processing")
            replacing_terminal_generation = True
        job = existing_job
        if job is None:
            job = ProcessingJob.objects.create(
                event=locked_event,
                run=run,
                photo=locked_photo,
                contract_version=contract_version,
                processor_type=processor_type,
                processor_version=processor_version,
                configuration=configuration,
                configuration_hash=configuration_hash,
                input_fingerprint=input_fingerprint,
                status=ProcessingJob.Status.QUEUED,
            )

        now = timezone.now()
        state.status = PhotoProcessingState.Status.QUEUED
        state.current_run = run
        state.current_job = job
        if replacing_terminal_generation:
            state.current_attempt = None
            state.accepted_attempt = None
            state.next_attempt_at = None
            state.processing_at = None
            state.succeeded_at = None
            state.failed_at = None
            state.cancelled_at = None
        state.queued_at = now
        update_fields = ["status", "current_run", "current_job", "queued_at", "updated_at"]
        if replacing_terminal_generation:
            update_fields.extend(
                [
                    "current_attempt",
                    "accepted_attempt",
                    "next_attempt_at",
                    "processing_at",
                    "succeeded_at",
                    "failed_at",
                    "cancelled_at",
                ]
            )
        state.save(update_fields=update_fields)
        if processor_type == CAPTURE_METADATA_PROCESSOR:
            transition_capture_time_projection(
                photo=locked_photo, state=state, accepted_attempt=None
            )
        return state


def reconcile_capture_metadata(
    *, limit: int = DEFAULT_RECONCILIATION_LIMIT
) -> list[PhotoProcessingState]:
    """Enroll one bounded, idempotent batch of eligible photos left without a current job."""
    return _reconcile(
        processor_type=CAPTURE_METADATA_PROCESSOR, limit=limit, processor_enabled=True
    )


def reconcile_bib_recognition(
    *, limit: int = DEFAULT_RECONCILIATION_LIMIT
) -> list[PhotoProcessingState]:
    """Enroll a bounded set selected only by persisted photo policy and publication."""
    if not 1 <= limit <= MAX_RECONCILIATION_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RECONCILIATION_LIMIT}")
    reconciled: list[PhotoProcessingState] = []
    candidates = Photo.objects.filter(
        bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1,
        original_key__isnull=False,
        original_key__gt="",
        original_size__isnull=False,
        original_content_type="image/jpeg",
    ).order_by("pk")
    for photo in candidates:
        if not _has_accepted_publication(photo):
            continue
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=BIB_RECOGNITION_PROCESSOR,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
        if state.status != PhotoProcessingState.Status.NOT_REQUESTED or state.current_job_id:
            continue
        reconciled.append(request_bib_recognition(photo))
        if len(reconciled) >= limit:
            break
    return reconciled


def reconcile_face_embedding(
    *, limit: int = DEFAULT_RECONCILIATION_LIMIT
) -> list[PhotoProcessingState]:
    """Enroll one bounded, idempotent batch of eligible photos for face embeddings."""
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
    if processor_type == CAPTURE_METADATA_PROCESSOR:
        return [request_capture_metadata(Photo.objects.get(pk=photo_id)) for photo_id in photo_ids]
    config = _reconcile_config(processor_type)
    return [
        request_processor(
            Photo.objects.get(pk=photo_id),
            processor_type=processor_type,
            contract_version=config["contract_version"],
            processor_version=config["processor_version"],
            configuration=config["configuration"],
            verified_source_etag=config["verified_source_etag"],
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
                event__timezone_name__isnull=False,
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


def _reconcile_config(processor_type: str) -> _ReconciliationProcessorConfig:
    if processor_type == FACE_EMBEDDING_PROCESSOR:
        from processing.services.face_quality import current_face_embedding_generation

        generation = current_face_embedding_generation()
        return {
            "contract_version": cast(int, generation["contract_version"]),
            "processor_version": cast(int, generation["processor_version"]),
            "configuration": cast(dict[str, object], generation["configuration"]),
            "verified_source_etag": None,
        }
    raise ValueError(f"unsupported processor_type: {processor_type}")


def _is_eligible(photo: Photo) -> bool:
    return bool(
        photo.original_key
        and photo.original_size is not None
        and photo.original_content_type == "image/jpeg"
    )


def _has_accepted_publication(photo: Photo) -> bool:
    if photo.processing_generation == Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1:
        return photo.gallery_media_policy == Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED
    if (
        photo.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_V1
        and photo.gallery_media_policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
    ):
        return _accepted_preview(photo) is not None
    if (
        photo.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        and photo.gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
    ):
        return _accepted_watermarked_preview(photo) is not None
    return False


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
    return _accepted_derivative(
        photo,
        variant="preview-small-v1",
        processor_type=GENERATE_PREVIEW_PROCESSOR,
    )


def _accepted_watermarked_preview(photo: Photo) -> PhotoDerivative | None:
    return _accepted_derivative(
        photo,
        variant="preview-watermarked-v1",
        processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    )


def _accepted_derivative(
    photo: Photo, *, variant: str, processor_type: str
) -> PhotoDerivative | None:
    derivative = PhotoDerivative.objects.filter(photo=photo, variant=variant).first()
    if derivative is None:
        return None
    try:
        state = PhotoProcessingState.objects.get(photo=photo, processor_type=processor_type)
    except PhotoProcessingState.DoesNotExist:
        return None
    if (
        state.status != PhotoProcessingState.Status.SUCCEEDED
        or state.accepted_attempt_id != derivative.accepted_attempt_id
    ):
        return None
    return derivative


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
    # Enrollment holds the event lock, so only the newest collecting run can be
    # partially filled. Do not recount every full run for a large backfill.
    run = query.order_by("-created_at", "-id").first()
    if run is not None and run.jobs.count() < configured_maximum:
        return run
    return EventProcessingRun.objects.create(
        event=event,
        contract_version=contract_version,
        processor_type=processor_type,
        processor_version=processor_version,
        configuration=configuration,
        configuration_hash=configuration_hash,
        status=EventProcessingRun.Status.COLLECTING,
        report={},
    )
