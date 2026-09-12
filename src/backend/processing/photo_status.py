"""Read-only, queryset-scoped photo processing status projection."""

from __future__ import annotations

from collections.abc import Iterable
from functools import reduce
from operator import or_
from typing import TypedDict, cast

from django.db.models import (
    BooleanField,
    Case,
    Count,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.fields import CharField
from picflow.models import Event, Photo

from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoProcessingState,
    ProcessingAttempt,
)
from processing.services.enrollment import (
    CAPTURE_METADATA_PROCESSOR_VERSION,
    CONTRACT_VERSION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
    GENERATE_PREVIEW_PROCESSOR_VERSION,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR_VERSION,
    LOCAL_ADAFACE_QUALITY_FACE_PROCESSOR_VERSION,
    PREVIEW_CONTRACT_VERSION,
    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
    QUALITY_FACE_CONTRACT_VERSION,
)

CATEGORY_PROCESSING = "processing"
CATEGORY_QUEUED = "queued"
CATEGORY_FAILED = "failed"
CATEGORY_CANCELLED = "cancelled"
CATEGORY_SUCCEEDED = "succeeded"
CATEGORY_NOT_STARTED = "not_started"
CATEGORY_NOT_REQUIRED = "not_required"

STAGE_WAITING = "waiting"
STAGE_NOT_STARTED = "not_started"
STAGE_NOT_REQUIRED = "not_required"

MAX_DETAIL_PHOTOS = 100

_STATE_QUEUED = cast(str, PhotoProcessingState.Status.QUEUED)
_STATE_PROCESSING = cast(str, PhotoProcessingState.Status.PROCESSING)
_STATE_RETRY_WAIT = cast(str, PhotoProcessingState.Status.RETRY_WAIT)
_STATE_SUCCEEDED = cast(str, PhotoProcessingState.Status.SUCCEEDED)
_STATE_FAILED = cast(str, PhotoProcessingState.Status.FAILED)
_STATE_CANCELLED = cast(str, PhotoProcessingState.Status.CANCELLED)

_CATEGORY_LABELS = {
    CATEGORY_PROCESSING: "Обрабатывается",
    CATEGORY_QUEUED: "Ожидает обработки",
    CATEGORY_FAILED: "Ошибка",
    CATEGORY_CANCELLED: "Остановлена",
    CATEGORY_SUCCEEDED: "Обработано",
    CATEGORY_NOT_STARTED: "Не запущена",
    CATEGORY_NOT_REQUIRED: "Не требуется",
}
_STAGE_LABELS = {
    CAPTURE_METADATA_PROCESSOR: "Метаданные",
    GENERATE_PREVIEW_PROCESSOR: "Превью",
    FACE_EMBEDDING_PROCESSOR: "Поиск лиц",
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR: "Водяной знак",
}
_STAGE_STATUS_LABELS = {
    _STATE_QUEUED: "В очереди",
    _STATE_PROCESSING: "Обрабатывается",
    _STATE_RETRY_WAIT: "Ожидает повторной попытки",
    _STATE_SUCCEEDED: "Обработано",
    _STATE_FAILED: "Ошибка",
    _STATE_CANCELLED: "Остановлена",
    STAGE_WAITING: "Ожидает предыдущий этап",
    STAGE_NOT_STARTED: "Не запущена",
    STAGE_NOT_REQUIRED: "Не требуется",
}
_PROCESSOR_TYPES = (
    CAPTURE_METADATA_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
)
_CATEGORY_VALUES = (
    CATEGORY_PROCESSING,
    CATEGORY_QUEUED,
    CATEGORY_FAILED,
    CATEGORY_CANCELLED,
    CATEGORY_SUCCEEDED,
    CATEGORY_NOT_STARTED,
    CATEGORY_NOT_REQUIRED,
)
_STAGE_STATUS_VALUES = (
    _STATE_QUEUED,
    _STATE_PROCESSING,
    _STATE_RETRY_WAIT,
    _STATE_SUCCEEDED,
    _STATE_FAILED,
    _STATE_CANCELLED,
    STAGE_WAITING,
    STAGE_NOT_STARTED,
    STAGE_NOT_REQUIRED,
)
_ACTIVE_STAGE_STATUSES = (
    _STATE_QUEUED,
    _STATE_PROCESSING,
    _STATE_RETRY_WAIT,
    STAGE_WAITING,
)
_PREVIEW_GENERATIONS = (
    cast(str, Photo.ProcessingGeneration.PREVIEW_FIRST_V1),
    cast(str, Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1),
)


class StageProcessingDetail(TypedDict):
    processor_type: str
    label: str
    status: str
    status_label: str
    error_code: str | None


class PhotoProcessingDetail(TypedDict):
    photo_id: str
    category: str
    category_label: str
    has_active_work: bool
    stages: list[StageProcessingDetail]


class PhotoProcessingSummary(TypedDict):
    total: int
    categories: dict[str, int]
    stages: dict[str, dict[str, int]]
    has_active_work: bool


def _any_stage(statuses: Iterable[str]) -> Q:
    values = tuple(statuses)
    return reduce(
        or_,
        (
            Q(**{f"_processing_{processor_type}_status__in": values})
            for processor_type in _PROCESSOR_TYPES
        ),
    )


def _required(processor_type: str) -> Q:
    private_photo = Q(original_key__isnull=False) & ~Q(original_key="")
    if processor_type in {CAPTURE_METADATA_PROCESSOR, FACE_EMBEDDING_PROCESSOR}:
        return private_photo
    if processor_type == GENERATE_PREVIEW_PROCESSOR:
        return private_photo & Q(processing_generation__in=_PREVIEW_GENERATIONS)
    return private_photo & Q(
        processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
    )


def _current_identity(processor_type: str) -> Q:
    if processor_type == CAPTURE_METADATA_PROCESSOR:
        return Q(
            current_job__contract_version=CONTRACT_VERSION,
            current_job__processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
        )
    if processor_type == GENERATE_PREVIEW_PROCESSOR:
        return Q(
            current_job__contract_version=PREVIEW_CONTRACT_VERSION,
            current_job__processor_version=GENERATE_PREVIEW_PROCESSOR_VERSION,
        )
    if processor_type == GENERATE_WATERMARKED_PREVIEW_PROCESSOR:
        return Q(
            current_job__contract_version=PREVIEW_CONTRACT_VERSION,
            current_job__processor_version=GENERATE_WATERMARKED_PREVIEW_PROCESSOR_VERSION,
        )
    return (
        Q(
            photo__processing_generation=Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1,
            current_job__contract_version=CONTRACT_VERSION,
            current_job__processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
        )
        | Q(
            photo__processing_generation__in=_PREVIEW_GENERATIONS,
            photo__event__face_search_generation=Event.FaceSearchGeneration.SFACE_V3,
            current_job__contract_version=PREVIEW_CONTRACT_VERSION,
            current_job__processor_version=PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
        )
        | Q(
            photo__processing_generation__in=_PREVIEW_GENERATIONS,
            photo__event__face_search_generation=Event.FaceSearchGeneration.ADAFACE_V5,
            current_job__contract_version=QUALITY_FACE_CONTRACT_VERSION,
            current_job__processor_version=LOCAL_ADAFACE_QUALITY_FACE_PROCESSOR_VERSION,
        )
    )


def _current_states(processor_type: str) -> QuerySet[PhotoProcessingState]:
    successful_result = Q(
        status=_STATE_SUCCEEDED,
        current_attempt_id=F("accepted_attempt_id"),
        current_job_id=F("accepted_attempt__job_id"),
        accepted_attempt__accepted=True,
        accepted_attempt__status=ProcessingAttempt.Status.SUCCEEDED,
    )
    return (
        PhotoProcessingState.objects.filter(
            photo_id=OuterRef("pk"),
            processor_type=processor_type,
        )
        .filter(_current_identity(processor_type))
        .filter(~Q(status=_STATE_SUCCEEDED) | successful_result)
    )


def _stage_status_expression(processor_type: str) -> Case:
    raw_status = f"_processing_{processor_type}_raw_status"
    cases = [
        When(~_required(processor_type), then=Value(STAGE_NOT_REQUIRED)),
        When(
            **{
                f"{raw_status}__in": (
                    _STATE_QUEUED,
                    _STATE_PROCESSING,
                    _STATE_RETRY_WAIT,
                    _STATE_SUCCEEDED,
                    _STATE_FAILED,
                    _STATE_CANCELLED,
                ),
                "then": F(raw_status),
            }
        ),
    ]
    if processor_type in {
        FACE_EMBEDDING_PROCESSOR,
        GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    }:
        cases.append(
            When(
                processing_generation__in=_PREVIEW_GENERATIONS,
                _processing_generate_preview_status__in=_ACTIVE_STAGE_STATUSES,
                then=Value(STAGE_WAITING),
            )
        )
    return Case(*cases, default=Value(STAGE_NOT_STARTED), output_field=CharField())


def annotate_photo_processing_status(photos: QuerySet[Photo]) -> QuerySet[Photo]:
    """Annotate a caller-scoped Photo queryset with one SQL-filterable category."""
    raw_annotations = {}
    error_annotations = {}
    for processor_type in _PROCESSOR_TYPES:
        current_states = _current_states(processor_type)
        raw_annotations[f"_processing_{processor_type}_raw_status"] = Subquery(
            current_states.values("status")[:1],
            output_field=CharField(),
        )
        error_annotations[f"_processing_{processor_type}_error_code"] = Subquery(
            current_states.filter(status=_STATE_FAILED).values("current_attempt__error_code")[:1],
            output_field=CharField(),
        )
    annotated = photos.annotate(**raw_annotations, **error_annotations)
    annotated = annotated.annotate(
        **{
            f"_processing_{processor_type}_status": _stage_status_expression(processor_type)
            for processor_type in _PROCESSOR_TYPES
        }
    )
    has_active_work = _any_stage(_ACTIVE_STAGE_STATUSES)
    all_finished = reduce(
        lambda left, right: left & right,
        (
            Q(
                **{
                    f"_processing_{processor_type}_status__in": (
                        _STATE_SUCCEEDED,
                        STAGE_NOT_REQUIRED,
                    )
                }
            )
            for processor_type in _PROCESSOR_TYPES
        ),
    )
    category = Case(
        When(_any_stage((_STATE_PROCESSING,)), then=Value(CATEGORY_PROCESSING)),
        When(
            _any_stage(
                (
                    _STATE_QUEUED,
                    _STATE_RETRY_WAIT,
                    STAGE_WAITING,
                )
            ),
            then=Value(CATEGORY_QUEUED),
        ),
        When(_any_stage((_STATE_FAILED,)), then=Value(CATEGORY_FAILED)),
        When(
            _any_stage((_STATE_CANCELLED,)),
            then=Value(CATEGORY_CANCELLED),
        ),
        When(all_finished & _required(CAPTURE_METADATA_PROCESSOR), then=Value(CATEGORY_SUCCEEDED)),
        When(_required(CAPTURE_METADATA_PROCESSOR), then=Value(CATEGORY_NOT_STARTED)),
        default=Value(CATEGORY_NOT_REQUIRED),
        output_field=CharField(),
    )
    return annotated.annotate(
        processing_category=category,
        has_active_work=Case(
            When(has_active_work, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def summarize_photo_processing(photos: QuerySet[Photo]) -> PhotoProcessingSummary:
    """Return grouped counts for exactly the caller-authorized Photo queryset."""
    annotated = annotate_photo_processing_status(photos.order_by())
    category_counts = {category: 0 for category in _CATEGORY_VALUES}
    for row in annotated.values("processing_category").annotate(count=Count("pk")):
        category_counts[cast(str, row["processing_category"])] = cast(int, row["count"])

    stage_counts: dict[str, dict[str, int]] = {}
    for processor_type in _PROCESSOR_TYPES:
        alias = f"_processing_{processor_type}_status"
        counts = {status: 0 for status in _STAGE_STATUS_VALUES}
        for row in annotated.values(alias).annotate(count=Count("pk")):
            counts[cast(str, row[alias])] = cast(int, row["count"])
        stage_counts[processor_type] = counts

    return {
        "total": sum(category_counts.values()),
        "categories": category_counts,
        "stages": stage_counts,
        "has_active_work": any(
            category_counts[category] for category in (CATEGORY_PROCESSING, CATEGORY_QUEUED)
        ),
    }


def photo_processing_details(photos: QuerySet[Photo]) -> list[PhotoProcessingDetail]:
    """Materialize status details for at most one bounded page of photos."""
    annotated = annotate_photo_processing_status(photos)
    fields = ["pk", "processing_category", "has_active_work"]
    for processor_type in _PROCESSOR_TYPES:
        fields.extend(
            (
                f"_processing_{processor_type}_status",
                f"_processing_{processor_type}_error_code",
            )
        )
    rows = list(annotated.values(*fields)[: MAX_DETAIL_PHOTOS + 1])
    if len(rows) > MAX_DETAIL_PHOTOS:
        raise ValueError(f"photo detail page must contain at most {MAX_DETAIL_PHOTOS} photos")

    details: list[PhotoProcessingDetail] = []
    for row in rows:
        stages: list[StageProcessingDetail] = []
        for processor_type in _PROCESSOR_TYPES:
            status = cast(str, row[f"_processing_{processor_type}_status"])
            if status == STAGE_NOT_REQUIRED:
                continue
            error_code = row[f"_processing_{processor_type}_error_code"]
            stages.append(
                {
                    "processor_type": processor_type,
                    "label": _STAGE_LABELS[processor_type],
                    "status": status,
                    "status_label": _STAGE_STATUS_LABELS[status],
                    "error_code": cast(str, error_code) if error_code else None,
                }
            )
        category = cast(str, row["processing_category"])
        details.append(
            {
                "photo_id": cast(str, row["pk"]),
                "category": category,
                "category_label": _CATEGORY_LABELS[category],
                "has_active_work": cast(bool, row["has_active_work"]),
                "stages": stages,
            }
        )
    return details
