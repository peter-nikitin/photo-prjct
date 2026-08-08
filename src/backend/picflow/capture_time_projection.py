"""Bounded repair and reconciliation for the Photo capture-time projection."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from django.db import transaction
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.results import parse_canonical_timestamp
from processing.services.jobs import transition_capture_time_projection

from picflow.models import Event, Photo

BATCH_SIZE = 500
MAX_IDENTITY_RETRIES = 3
SUPPORTED_PROCESSOR_VERSION = 2
EXPECTED_EVENT_ID = 9
EXPECTED_EVENT_NAME = "Cyclingrace Вечернее Садовое"
EXPECTED_EVENT_TIMEZONE = "Europe/Moscow"
EXPECTED_EVENT_PHOTO_COUNT = 17_043


@dataclass(frozen=True)
class PhotoIdentity:
    photo_id: str
    event_id: int
    state_id: int | None
    run_id: UUID | None
    job_id: UUID | None
    accepted_attempt_id: UUID | None


@dataclass(frozen=True)
class ProjectionExpectation:
    capture_time: datetime | None
    source_attempt: ProcessingAttempt | None


def discover_photo_identity(photo_id: str) -> PhotoIdentity | None:
    """Read only identifiers; a repair must never publish from this snapshot."""
    photo = Photo.objects.filter(pk=photo_id).values("id", "event_id").first()
    if photo is None:
        return None
    state = (
        PhotoProcessingState.objects.filter(
            photo_id=photo_id, processor_type=CAPTURE_METADATA_PROCESSOR
        )
        .values("id", "current_run_id", "current_job_id", "accepted_attempt_id")
        .first()
    )
    return PhotoIdentity(
        photo_id=photo["id"],
        event_id=photo["event_id"],
        state_id=state["id"] if state is not None else None,
        run_id=state["current_run_id"] if state is not None else None,
        job_id=state["current_job_id"] if state is not None else None,
        accepted_attempt_id=state["accepted_attempt_id"] if state is not None else None,
    )


def rebuild_events(*, event_id: int | None, apply: bool) -> dict[str, int]:
    totals = _empty_rebuild_totals()
    for scoped_event_id in iter_event_ids(event_id=event_id):
        event_totals = rebuild_event(scoped_event_id, apply=apply)
        for key, value in event_totals.items():
            totals[key] += value
    return totals


def iter_event_ids(*, event_id: int | None) -> Iterator[int]:
    events = Event.objects.order_by("pk")
    if event_id is not None:
        events = events.filter(pk=event_id)
        if not events.exists():
            raise Event.DoesNotExist
    yield from events.values_list("pk", flat=True).iterator(chunk_size=BATCH_SIZE)


def rebuild_event(event_id: int, *, apply: bool) -> dict[str, int]:
    totals = _empty_rebuild_totals()
    photo_ids = Photo.objects.filter(event_id=event_id).order_by("pk").values_list("pk", flat=True)
    for photo_id in photo_ids.iterator(chunk_size=BATCH_SIZE):
        totals["photos"] += 1
        totals["batches"] = ceil(totals["photos"] / BATCH_SIZE)
        outcome, retries = rebuild_photo(photo_id, apply=apply)
        totals[outcome] += 1
        totals["retries"] += retries
    totals["events"] = 1
    return totals


def rebuild_photo(photo_id: str, *, apply: bool) -> tuple[str, int]:
    for retry in range(MAX_IDENTITY_RETRIES + 1):
        identity = discover_photo_identity(photo_id)
        if identity is None:
            return "skipped", retry
        with transaction.atomic():
            event = _lock_repair_row(Event, pk=identity.event_id)
            run = _locked_or_none(EventProcessingRun, identity.run_id)
            job = _locked_or_none(ProcessingJob, identity.job_id)
            photo = _lock_repair_row(Photo, pk=identity.photo_id)
            state = _locked_state(photo, identity.state_id)
            attempt = _locked_or_none(ProcessingAttempt, identity.accepted_attempt_id)
            if _locked_identity(photo=photo, state=state) != identity:
                continue
            expected = _expected_projection(
                event=event, run=run, job=job, photo=photo, state=state, attempt=attempt
            )
            if _matches(photo, expected):
                return "unchanged", retry
            if apply:
                if state is None:
                    _clear_projection(photo)
                else:
                    transition_capture_time_projection(
                        photo=photo, state=state, accepted_attempt=expected.source_attempt
                    )
            return "changed", retry
    return "exhausted", MAX_IDENTITY_RETRIES


def report_events(*, event_id: int | None) -> dict[str, object]:
    totals = _empty_report_counts()
    event_nine: dict[str, object] | None = None
    event_count = 0
    for scoped_event_id in iter_event_ids(event_id=event_id):
        event_count += 1
        event = Event.objects.get(pk=scoped_event_id)
        counts = report_event(event)
        for key in totals:
            totals[key] += counts[key]
        if event.pk == EXPECTED_EVENT_ID:
            event_nine = _event_nine_report(event, counts)
    clean = all(
        totals[key] == 0
        for key in (
            "missing",
            "mismatching",
            "stale",
            "extra",
            "partial_pair",
            "unsupported_version",
        )
    )
    if event_nine is not None:
        clean = clean and bool(event_nine["accepted"])
    elif event_id is None:
        clean = False
        event_nine = {
            "accepted": False,
            "exact_source_value_pairs": 0,
            "expected_source_value_pairs": EXPECTED_EVENT_PHOTO_COUNT,
        }
    return {"clean": clean, "counts": totals, "event_9": event_nine, "events": event_count}


def report_event(event: Event) -> dict[str, int]:
    counts = _empty_report_counts()
    photo_ids = Photo.objects.filter(event=event).order_by("pk").values_list("pk", flat=True)
    for photo_id in photo_ids.iterator(chunk_size=BATCH_SIZE):
        photo = Photo.objects.select_related("capture_time_source_attempt").get(pk=photo_id)
        state = (
            PhotoProcessingState.objects.select_related(
                "current_run", "current_job", "accepted_attempt"
            )
            .filter(photo=photo, processor_type=CAPTURE_METADATA_PROCESSOR)
            .first()
        )
        run = state.current_run if state is not None else None
        job = state.current_job if state is not None else None
        attempt = state.accepted_attempt if state is not None else None
        qualifying = _is_qualifying_attempt(
            event=event, run=run, job=job, photo=photo, state=state, attempt=attempt
        )
        expected = _expected_projection(
            event=event,
            run=run,
            job=job,
            photo=photo,
            state=state,
            attempt=attempt,
        )
        counts["photos"] += 1
        if expected.source_attempt is not None:
            counts["qualifying_non_null"] += 1
        elif qualifying and attempt is not None and attempt.result.get("capture_time") is None:
            counts["qualifying_null"] += 1
        category = _projection_category(photo, expected)
        if category is not None:
            counts[category] += 1
        if photo.capture_time is not None and photo.capture_time_source_attempt_id is not None:
            counts["projection_pairs"] += 1
            if _matches(photo, expected):
                counts["exact_source_value_pairs"] += 1
    return counts


def _locked_or_none(model, pk):
    return _lock_repair_row(model, pk=pk) if pk is not None else None


def _lock_repair_row(model, **lookup):
    """Take one repair lock; callers retain the global Event-to-Attempt order."""
    return model.objects.select_for_update().get(**lookup)


def _locked_state(photo: Photo, state_id: int | None) -> PhotoProcessingState | None:
    if state_id is None:
        return (
            PhotoProcessingState.objects.select_for_update()
            .filter(photo=photo, processor_type=CAPTURE_METADATA_PROCESSOR)
            .first()
        )
    return _lock_repair_row(PhotoProcessingState, pk=state_id)


def _locked_identity(*, photo: Photo, state: PhotoProcessingState | None) -> PhotoIdentity:
    return PhotoIdentity(
        photo_id=photo.pk,
        event_id=photo.event_id,
        state_id=state.pk if state is not None else None,
        run_id=state.current_run_id if state is not None else None,
        job_id=state.current_job_id if state is not None else None,
        accepted_attempt_id=state.accepted_attempt_id if state is not None else None,
    )


def _expected_projection(
    *,
    event: Event,
    run: EventProcessingRun | None,
    job: ProcessingJob | None,
    photo: Photo,
    state: PhotoProcessingState | None,
    attempt: ProcessingAttempt | None,
) -> ProjectionExpectation:
    if not _is_qualifying_attempt(
        event=event, run=run, job=job, photo=photo, state=state, attempt=attempt
    ):
        return ProjectionExpectation(capture_time=None, source_attempt=None)
    assert attempt is not None
    capture_time = _parse_capture_time(attempt.result.get("capture_time"))
    if capture_time is None:
        return ProjectionExpectation(capture_time=None, source_attempt=None)
    return ProjectionExpectation(capture_time=capture_time, source_attempt=attempt)


def _is_qualifying_attempt(
    *,
    event: Event,
    run: EventProcessingRun | None,
    job: ProcessingJob | None,
    photo: Photo,
    state: PhotoProcessingState | None,
    attempt: ProcessingAttempt | None,
) -> bool:
    return bool(
        state is not None
        and run is not None
        and job is not None
        and attempt is not None
        and state.status == PhotoProcessingState.Status.SUCCEEDED
        and state.current_run_id == run.pk
        and state.current_job_id == job.pk
        and state.current_attempt_id == attempt.pk
        and state.accepted_attempt_id == attempt.pk
        and event.pk == photo.event_id == run.event_id == job.event_id == attempt.event_id
        and photo.pk == job.photo_id == attempt.photo_id == state.photo_id
        and run.pk == job.run_id == attempt.run_id
        and state.processor_type == CAPTURE_METADATA_PROCESSOR
        and run.processor_type == CAPTURE_METADATA_PROCESSOR
        and job.processor_type == CAPTURE_METADATA_PROCESSOR
        and attempt.processor_type == CAPTURE_METADATA_PROCESSOR
        and run.processor_version == SUPPORTED_PROCESSOR_VERSION
        and job.processor_version == SUPPORTED_PROCESSOR_VERSION
        and attempt.processor_version == SUPPORTED_PROCESSOR_VERSION
        and job.status == ProcessingJob.Status.SUCCEEDED
        and attempt.status == ProcessingAttempt.Status.SUCCEEDED
        and attempt.accepted
    )


def _parse_capture_time(value: object) -> datetime | None:
    parsed = parse_canonical_timestamp(value)
    return parsed.astimezone(UTC) if parsed is not None else None


def _matches(photo: Photo, expected: ProjectionExpectation) -> bool:
    return photo.capture_time == expected.capture_time and photo.capture_time_source_attempt_id == (
        expected.source_attempt.pk if expected.source_attempt is not None else None
    )


def _clear_projection(photo: Photo) -> None:
    photo.capture_time = None
    photo.capture_time_source_attempt = None
    photo.save(update_fields=["capture_time", "capture_time_source_attempt"])


def _projection_category(photo: Photo, expected: ProjectionExpectation) -> str | None:
    has_time = photo.capture_time is not None
    has_source = photo.capture_time_source_attempt_id is not None
    if has_time != has_source:
        return "partial_pair"
    if not has_time:
        return "missing" if expected.source_attempt is not None else None
    if _matches(photo, expected):
        return None
    source = photo.capture_time_source_attempt
    if source is not None and source.processor_version != SUPPORTED_PROCESSOR_VERSION:
        return "unsupported_version"
    if expected.source_attempt is not None and source is not None:
        if source.pk == expected.source_attempt.pk:
            return "mismatching"
        if source.photo_id == photo.pk:
            return "stale"
    if source is not None and source.photo_id == photo.pk:
        return "stale"
    if expected.source_attempt is not None:
        return "mismatching"
    return "extra"


def _event_nine_report(event: Event, counts: dict[str, int]) -> dict[str, object]:
    accepted = bool(
        event.name == EXPECTED_EVENT_NAME
        and event.publication_status == Event.PublicationStatus.PUBLISHED
        and event.timezone_name == EXPECTED_EVENT_TIMEZONE
        and counts["photos"] == EXPECTED_EVENT_PHOTO_COUNT
        and counts["qualifying_non_null"] == EXPECTED_EVENT_PHOTO_COUNT
        and counts["projection_pairs"] == EXPECTED_EVENT_PHOTO_COUNT
        and counts["exact_source_value_pairs"] == EXPECTED_EVENT_PHOTO_COUNT
        and all(
            counts[key] == 0
            for key in (
                "missing",
                "mismatching",
                "stale",
                "extra",
                "partial_pair",
                "unsupported_version",
            )
        )
    )
    return {
        "accepted": accepted,
        "exact_source_value_pairs": counts["exact_source_value_pairs"],
        "expected_source_value_pairs": EXPECTED_EVENT_PHOTO_COUNT,
    }


def _empty_rebuild_totals() -> dict[str, int]:
    return {
        "batches": 0,
        "changed": 0,
        "events": 0,
        "exhausted": 0,
        "photos": 0,
        "retries": 0,
        "skipped": 0,
        "unchanged": 0,
    }


def _empty_report_counts() -> dict[str, int]:
    return {
        "exact_source_value_pairs": 0,
        "extra": 0,
        "mismatching": 0,
        "missing": 0,
        "partial_pair": 0,
        "photos": 0,
        "projection_pairs": 0,
        "qualifying_non_null": 0,
        "qualifying_null": 0,
        "stale": 0,
        "unsupported_version": 0,
    }
