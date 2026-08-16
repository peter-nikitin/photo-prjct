from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import NoReturn, TypedDict, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_BENCHMARK_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    REPORT_JSON_MAX_BYTES,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingJob,
)
from processing.services.jobs import transition_capture_time_projection

CONTRACT_VERSION = 1
CAPTURE_METADATA_PROCESSOR_VERSION = 2
FACE_EMBEDDING_PROCESSOR_VERSION = 1
PREVIEW_CONTRACT_VERSION = 2
GENERATE_PREVIEW_PROCESSOR_VERSION = 1
PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION = 3
QUALITY_FACE_CONTRACT_VERSION = 3
HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION = 3
QUALITY_FACE_PROCESSOR_VERSION = 4
FACE_EMBEDDING_BENCHMARK_CONTRACT_VERSION = 3
FACE_EMBEDDING_BENCHMARK_PROCESSOR_VERSION = 1
FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES = 128 * 1024

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
    "report_max_bytes": REPORT_JSON_MAX_BYTES,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "face_embedding": {
        "model": "sface",
        "min_face_px": 32,
        "max_faces_per_photo": 32,
        "normalize_embeddings": True,
    },
    "worker": {
        "api_response_max_bytes": FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": 100_000_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES,
    },
}

SCRFD_FACE_EMBEDDING_CONFIGURATION: dict[str, object] = {
    **FACE_EMBEDDING_CONFIGURATION,
    "face_embedding": {
        **cast(dict[str, object], FACE_EMBEDDING_CONFIGURATION["face_embedding"]),
        "detection_threshold": 0.5,
    },
}

# Provisional calibration points for private benchmark execution only.  Task 6 must replace these
# values and record a matching approval before this generation can be activated for customer search.
FACE_EMBEDDING_QUALITY_CONFIGURATION: dict[str, object] = {
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
        "max_faces": 32,
        "detection_threshold": 0.75,
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
        "api_response_max_bytes": FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": 100_000_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES,
    },
}

FACE_EMBEDDING_BENCHMARK_CONFIGURATION: dict[str, object] = {
    **FACE_EMBEDDING_CONFIGURATION,
    "max_cohort_size": 500,
    "benchmark": {
        "label": "baseline",
        "source_mode": "event",
        "source_run_id": None,
        "requested_count": 1,
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


@dataclass(frozen=True)
class FaceEmbeddingGenerationApproval:
    """Bounded non-biometric evidence authorizing one event's reviewed candidate identity."""

    event_slug: str
    photo_count: int
    configuration_hash: str
    preview_manifest_hash: str
    local_preview_projection_hash: str
    accepted_preview_cohort_hash: str
    accepted_preview_crosswalk_hash: str
    accepted_preview_crosswalk_entry_count: int
    accepted_preview_crosswalk_sha_mismatch_count: int
    comparison_manifest_hash: str
    yunet_model_hash: str
    sface_model_hash: str
    job_count: int
    attempt_count: int
    projection_count: int
    technical_failure_count: int
    kept_face_count: int
    quality_rejected_face_count: int
    approved: bool


# The explicit maintainer review covers these exact, content-addressed local artifacts.  It does
# not claim a person-labelled benchmark or fabricate unobserved loss categories.
FACE_EMBEDDING_QUALITY_APPROVAL = FaceEmbeddingGenerationApproval(
    event_slug="cyclingrace-vechernee-sadovoe",
    photo_count=17_043,
    configuration_hash="dfe32ba0c5914db5a5720046ac5220659155a370a3d9abab766410c41873919a",
    preview_manifest_hash="62f071941cd8281745256ed6906f37cbfdac29996f20fd6a992c7f486783d879",
    local_preview_projection_hash=(
        "a98b5d13152683419c722a115045037fdf883a1f5cdcc3e47a2bddf5291b7d63"
    ),
    accepted_preview_cohort_hash=(
        "6701b7436e1b00b64e701791983a0c9c1d26bcddd56f93a36dd0923aa6bc1034"
    ),
    accepted_preview_crosswalk_hash=(
        "055d7c72614deb3b87b607f467c16365ee6e125be005e9e8f5cf2e910ec56d51"
    ),
    accepted_preview_crosswalk_entry_count=17_043,
    accepted_preview_crosswalk_sha_mismatch_count=17_043,
    comparison_manifest_hash="043ce5c02cd6df901f16096c2637c3a26b3b96171a9e9538b439cee12abca0a6",
    yunet_model_hash="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    sface_model_hash="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    job_count=17_043,
    attempt_count=17_043,
    projection_count=17_043,
    technical_failure_count=0,
    kept_face_count=37_573,
    quality_rejected_face_count=18_610,
    approved=True,
)


ACCEPTED_PREVIEW_PROJECTION_FIELDS = (
    "byte_size",
    "height",
    "oriented_source_height",
    "oriented_source_width",
    "photo_id",
    "sha256",
    "width",
)


def accepted_preview_projection(event: Event) -> tuple[dict[str, object], ...]:
    """Project the exact accepted derivatives into the reviewed non-biometric v1 hash format."""
    return tuple(
        PhotoDerivative.objects.filter(
            photo__event=event,
            variant="preview-small-v1",
            photo__processing_states__processor_type=GENERATE_PREVIEW_PROCESSOR,
            photo__processing_states__status=PhotoProcessingState.Status.SUCCEEDED,
            photo__processing_states__accepted_attempt_id=F("accepted_attempt_id"),
        )
        .order_by("photo_id")
        .values(*ACCEPTED_PREVIEW_PROJECTION_FIELDS)
    )


def accepted_preview_cohort_hash(event: Event) -> str:
    """Hash the canonical ordered accepted-PhotoDerivative projection for one event."""
    encoded = json.dumps(
        accepted_preview_projection(event), separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def candidate_face_embedding_cohort(event: Event) -> list[Photo]:
    """Return the event's current, accepted preview-backed candidate cohort without writes."""
    photo_ids = [row["photo_id"] for row in accepted_preview_projection(event)]
    return list(Photo.objects.filter(pk__in=photo_ids).order_by("pk"))


def validate_face_embedding_candidate_enrollment(
    event: Event,
    *,
    approval: FaceEmbeddingGenerationApproval | None = None,
) -> list[Photo]:
    """Fail closed unless the current event cohort matches its exact reviewed approval."""
    selected_approval = approval or FACE_EMBEDDING_QUALITY_APPROVAL
    candidate_configuration_hash = _configuration_hash(FACE_EMBEDDING_QUALITY_CONFIGURATION)
    if (
        selected_approval is None
        or selected_approval.approved is not True
        or selected_approval.event_slug != event.slug
        or selected_approval.configuration_hash != candidate_configuration_hash
        or any(
            not _is_sha256(value)
            for value in (
                selected_approval.configuration_hash,
                selected_approval.preview_manifest_hash,
                selected_approval.local_preview_projection_hash,
                selected_approval.accepted_preview_cohort_hash,
                selected_approval.accepted_preview_crosswalk_hash,
                selected_approval.comparison_manifest_hash,
                selected_approval.yunet_model_hash,
                selected_approval.sface_model_hash,
            )
        )
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (
                selected_approval.photo_count,
                selected_approval.job_count,
                selected_approval.attempt_count,
                selected_approval.projection_count,
                selected_approval.accepted_preview_crosswalk_entry_count,
                selected_approval.accepted_preview_crosswalk_sha_mismatch_count,
                selected_approval.technical_failure_count,
                selected_approval.kept_face_count,
                selected_approval.quality_rejected_face_count,
            )
        )
        or len(
            {
                selected_approval.photo_count,
                selected_approval.job_count,
                selected_approval.attempt_count,
                selected_approval.projection_count,
            }
        )
        != 1
        or selected_approval.technical_failure_count != 0
        or selected_approval.accepted_preview_crosswalk_entry_count != selected_approval.photo_count
        or selected_approval.accepted_preview_crosswalk_sha_mismatch_count
        != selected_approval.photo_count
    ):
        raise ValueError("candidate approval identity is invalid")
    cohort = candidate_face_embedding_cohort(event)
    if len(cohort) != selected_approval.photo_count:
        raise ValueError("candidate approval does not match the accepted preview cohort")
    if accepted_preview_cohort_hash(event) != selected_approval.accepted_preview_cohort_hash:
        raise ValueError("candidate approval does not match the accepted preview cohort hash")
    return cohort


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
            configuration=SCRFD_FACE_EMBEDDING_CONFIGURATION,
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


def request_face_embedding_candidate_enqueue(photo: Photo) -> NoReturn:
    """Reject YuNet-calibrated quality work until SCRFD has a new immutable generation."""
    del photo
    raise ValueError("SCRFD quality generation is not approved")


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


def create_face_embedding_benchmark_run(
    *,
    event: Event,
    photos: list[Photo],
    label: str,
    source_run_id: str | None,
) -> EventProcessingRun:
    """Create one isolated benchmark cohort without using generic reconciliation."""
    if not photos or len(photos) > 500:
        raise ValueError("benchmark cohort must contain between 1 and 500 photos")
    if any(photo.event_id != event.id or not _is_eligible(photo) for photo in photos):
        raise ValueError("benchmark cohort contains an ineligible photo")
    if len({photo.pk for photo in photos}) != len(photos):
        raise ValueError("benchmark cohort contains duplicate photos")
    configuration = {
        **FACE_EMBEDDING_BENCHMARK_CONFIGURATION,
        "benchmark": {
            "label": label,
            "source_mode": "replay" if source_run_id else "event",
            "source_run_id": source_run_id,
            "requested_count": len(photos),
        },
    }
    configuration_hash = _configuration_hash(configuration)
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        run = EventProcessingRun.objects.create(
            event=locked_event,
            contract_version=FACE_EMBEDDING_BENCHMARK_CONTRACT_VERSION,
            processor_type=FACE_EMBEDDING_BENCHMARK_PROCESSOR,
            processor_version=FACE_EMBEDDING_BENCHMARK_PROCESSOR_VERSION,
            configuration=configuration,
            configuration_hash=configuration_hash,
        )
        now = timezone.now()
        for photo in photos:
            state, _ = PhotoProcessingState.objects.select_for_update().get_or_create(
                photo=photo,
                processor_type=FACE_EMBEDDING_BENCHMARK_PROCESSOR,
                defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
            )
            if state.status in {
                PhotoProcessingState.Status.QUEUED,
                PhotoProcessingState.Status.PROCESSING,
                PhotoProcessingState.Status.RETRY_WAIT,
            }:
                raise ValueError("benchmark photo already has active benchmark work")
            job = ProcessingJob.objects.create(
                event=locked_event,
                run=run,
                photo=photo,
                contract_version=FACE_EMBEDDING_BENCHMARK_CONTRACT_VERSION,
                processor_type=FACE_EMBEDDING_BENCHMARK_PROCESSOR,
                processor_version=FACE_EMBEDDING_BENCHMARK_PROCESSOR_VERSION,
                configuration=configuration,
                configuration_hash=configuration_hash,
                input_fingerprint=_input_fingerprint(photo, verified_source_etag=None),
            )
            state.status = PhotoProcessingState.Status.QUEUED
            state.current_run = run
            state.current_job = job
            state.current_attempt = None
            state.accepted_attempt = None
            state.queued_at = now
            state.save(
                update_fields=[
                    "status",
                    "current_run",
                    "current_job",
                    "current_attempt",
                    "accepted_attempt",
                    "queued_at",
                    "updated_at",
                ]
            )
    return run


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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
