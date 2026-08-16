"""Strict validation for quality-gated gallery face terminal results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import Count, Q
from picflow.models import Event

if TYPE_CHECKING:
    from processing.models import ProcessingAttempt

QUALITY_FACE_CONTRACT_VERSION = 3
HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION = 3
QUALITY_FACE_PROCESSOR_VERSION = 4
QUALITY_FACE_PROCESSOR_VERSIONS = frozenset(
    {HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION, QUALITY_FACE_PROCESSOR_VERSION}
)
MAX_QUALITY_FACES = 64
EMBEDDING_DIMENSIONS = 128
MAX_SHARPNESS = 1_040_400.0

QUALITY_REJECTION_REASONS = frozenset(
    {
        "too_small",
        "severe_blur",
        "borderline_blur",
        "small_relative_area",
        "low_confidence",
    }
)
TECHNICAL_FAILURE_REASONS = frozenset({"invalid_face_quality", "model_inference_error"})
_WARNING_CODES = frozenset(
    {
        "face_embedding_failed",
        "face_quality_failed",
        "faces_truncated",
        "no_faces_detected",
        "no_valid_faces",
    }
)
_TIMING_FIELDS = frozenset({"decode_ms", "model_load_ms", "detect_ms", "embed_ms", "total_ms"})
_QUALITY_FIELDS = frozenset(
    {
        "algorithm_version",
        "crop_size",
        "confidence",
        "minimum_side_px",
        "relative_area",
        "sharpness",
        "decision",
        "reasons",
    }
)
_FACE_FIELDS = frozenset({"index", "bbox", "confidence", "landmarks", "status"})
_RESULT_FIELDS = frozenset(
    {
        "model",
        "face_count",
        "faces",
        "has_single_query_face_usable",
        "warnings",
        "timings",
    }
)
_PREVIEW_RESULT_FIELDS = _RESULT_FIELDS | {"input_geometry"}
_ORIGINAL_FINGERPRINT_FIELDS = frozenset(
    {
        "original_key",
        "original_size",
        "original_content_type",
        "verified_source_etag",
        "version_evidence",
    }
)
_PREVIEW_FINGERPRINT_FIELDS = frozenset(
    {
        "object_key",
        "object_size",
        "object_content_type",
        "object_etag",
        "media_kind",
        "pixel_width",
        "pixel_height",
    }
)
_ORIGINAL_KEY = re.compile(r"originals/[0-9a-f]{32}")
_PUBLISHED_PREVIEW_KEY = re.compile(
    r"derivatives/previews/(?P<photo_id>[A-Za-z0-9_-]{1,32})/preview-small-v1/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-"
    r"[0-9a-f]{64}\.jpg"
)
_FACE_CONFIGURATION_FIELDS = frozenset(
    {"model", "max_faces", "detection_threshold", "normalize_embeddings", "quality"}
)
_QUALITY_CONFIGURATION_FIELDS = frozenset(
    {
        "algorithm_version",
        "crop_size",
        "minimum_face_px",
        "severe_blur_threshold",
        "borderline_blur_threshold",
        "minimum_relative_area",
        "minimum_confidence",
    }
)


class FaceQualityResultError(ValueError):
    """A versioned quality terminal result is incomplete or contradictory."""


@dataclass(frozen=True)
class QualityFaceConfiguration:
    maximum_faces: int
    algorithm_version: str
    crop_size: int
    minimum_face_px: int
    severe_blur_threshold: float
    borderline_blur_threshold: float
    minimum_relative_area: float
    minimum_confidence: float


@dataclass(frozen=True)
class ValidatedQualityFace:
    index: int
    bbox: list[float]
    confidence: float
    landmarks: list[list[float]]
    status: str
    quality: dict[str, Any] | None
    embedding: list[float] | None
    error_code: str | None


@dataclass(frozen=True)
class ValidatedQualityResult:
    model: str
    faces: tuple[ValidatedQualityFace, ...]
    has_single_query_face_usable: bool
    warnings: list[str]
    timings: dict[str, int]

    @property
    def detected_count(self) -> int:
        return len(self.faces)

    @property
    def kept_count(self) -> int:
        return sum(face.status == "kept" for face in self.faces)

    @property
    def quality_rejected_count(self) -> int:
        return sum(face.status == "quality_rejected" for face in self.faces)

    @property
    def embedded_count(self) -> int:
        return sum(face.embedding is not None for face in self.faces)

    @property
    def technical_failed_count(self) -> int:
        return sum(face.status == "technical_failed" for face in self.faces)

    @property
    def rejection_reasons(self) -> dict[str, int]:
        reasons: Counter[str] = Counter()
        for face in self.faces:
            if face.status == "quality_rejected" and face.quality is not None:
                reasons.update(face.quality["reasons"])
        return dict(sorted(reasons.items()))

    @property
    def technical_failure_reasons(self) -> dict[str, int]:
        reasons = Counter(
            face.error_code
            for face in self.faces
            if face.status == "technical_failed" and face.error_code is not None
        )
        return dict(sorted(reasons.items()))


def validate_quality_face_result(value: object, *, configuration: object) -> ValidatedQualityResult:
    """Return one normalized v3 result or raise without producing partial evidence."""
    quality_configuration = _validate_quality_configuration(configuration)
    if not isinstance(value, dict) or set(value) not in {_RESULT_FIELDS, _PREVIEW_RESULT_FIELDS}:
        raise FaceQualityResultError("invalid result fields")
    if value["model"] != "sface":
        raise FaceQualityResultError("invalid model")
    face_count = value["face_count"]
    faces = value["faces"]
    if not (
        isinstance(face_count, int)
        and not isinstance(face_count, bool)
        and 0 <= face_count <= quality_configuration.maximum_faces
        and isinstance(faces, list)
        and len(faces) == face_count
    ):
        raise FaceQualityResultError("invalid face count")
    warnings = value["warnings"]
    if not (
        isinstance(warnings, list)
        and len(warnings) <= 8
        and all(isinstance(code, str) and code in _WARNING_CODES for code in warnings)
        and len(set(warnings)) == len(warnings)
    ):
        raise FaceQualityResultError("invalid warnings")
    timings = _validate_timings(value["timings"])
    validated_faces = tuple(
        _validate_face(face, configuration=quality_configuration) for face in faces
    )
    if [face.index for face in validated_faces] != list(range(face_count)):
        raise FaceQualityResultError("face indexes must be contiguous")
    has_single = value["has_single_query_face_usable"]
    if not isinstance(has_single, bool) or has_single != (
        sum(face.status == "kept" for face in validated_faces) == 1
    ):
        raise FaceQualityResultError("invalid single-face usability")
    return ValidatedQualityResult(
        model="sface",
        faces=validated_faces,
        has_single_query_face_usable=has_single,
        warnings=list(warnings),
        timings=timings,
    )


def quality_face_claim_input_geometry(
    *, photo_id: str, processor_version: int, input_fingerprint: object
) -> dict[str, int | str] | None:
    """Validate one versioned quality input and bind previews to accepted media."""
    from processing.models import (  # noqa: PLC0415
        GENERATE_PREVIEW_PROCESSOR,
        PhotoDerivative,
        PhotoProcessingState,
    )

    if not isinstance(input_fingerprint, dict):
        raise FaceQualityResultError("invalid quality input fingerprint")
    if set(input_fingerprint) == _ORIGINAL_FINGERPRINT_FIELDS:
        if processor_version != HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION:
            raise FaceQualityResultError("quality v4 requires preview input")
        key = input_fingerprint["original_key"]
        size = input_fingerprint["original_size"]
        etag = input_fingerprint["verified_source_etag"]
        evidence = input_fingerprint["version_evidence"]
        if not (
            isinstance(key, str)
            and _ORIGINAL_KEY.fullmatch(key) is not None
            and _positive_int(size)
            and input_fingerprint["original_content_type"] == "image/jpeg"
            and (etag is None or isinstance(etag, str))
            and evidence in {"verified_source_etag", "unavailable"}
            and ((evidence == "verified_source_etag") == isinstance(etag, str))
        ):
            raise FaceQualityResultError("invalid quality input fingerprint")
        return None
    if set(input_fingerprint) != _PREVIEW_FINGERPRINT_FIELDS:
        raise FaceQualityResultError("invalid quality input fingerprint")
    key = input_fingerprint["object_key"]
    size = input_fingerprint["object_size"]
    width = input_fingerprint["pixel_width"]
    height = input_fingerprint["pixel_height"]
    key_match = _PUBLISHED_PREVIEW_KEY.fullmatch(key) if isinstance(key, str) else None
    if not (
        key_match is not None
        and key_match.group("photo_id") == photo_id
        and _positive_int(size)
        and input_fingerprint["object_content_type"] == "image/jpeg"
        and input_fingerprint["object_etag"] is None
        and input_fingerprint["media_kind"] == "preview-small-v1"
        and _positive_int(width)
        and _positive_int(height)
    ):
        raise FaceQualityResultError("invalid quality input fingerprint")
    derivative = PhotoDerivative.objects.filter(
        photo_id=photo_id,
        variant="preview-small-v1",
        final_key=key,
        byte_size=size,
        content_type="image/jpeg",
        width=width,
        height=height,
        accepted_attempt_id__isnull=False,
    ).first()
    if (
        derivative is None
        or not PhotoProcessingState.objects.filter(
            photo_id=photo_id,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            status=PhotoProcessingState.Status.SUCCEEDED,
            accepted_attempt_id=derivative.accepted_attempt_id,
        ).exists()
    ):
        raise FaceQualityResultError("quality preview input is not the accepted derivative")
    return {
        "coordinate_space": "preview-small-v1",
        "pixel_width": derivative.width,
        "pixel_height": derivative.height,
        "oriented_source_width": derivative.oriented_source_width,
        "oriented_source_height": derivative.oriented_source_height,
    }


def quality_face_result_geometry(
    attempt: ProcessingAttempt, result: object
) -> dict[str, int | float | str]:
    """Verify worker geometry against claimed quality media and return persisted coordinates."""
    if not isinstance(result, dict):
        raise FaceQualityResultError("invalid quality result")
    expected = quality_face_claim_input_geometry(
        photo_id=attempt.photo_id,
        processor_version=attempt.processor_version,
        input_fingerprint=attempt.input_fingerprint,
    )
    if expected is None:
        if "input_geometry" in result:
            raise FaceQualityResultError("original quality result must not contain input geometry")
        return {}
    if result.get("input_geometry") != expected:
        raise FaceQualityResultError(
            "quality preview result geometry disagrees with accepted input"
        )
    pixel_width = expected["pixel_width"]
    pixel_height = expected["pixel_height"]
    source_width = expected["oriented_source_width"]
    source_height = expected["oriented_source_height"]
    if not (
        isinstance(pixel_width, int)
        and isinstance(pixel_height, int)
        and isinstance(source_width, int)
        and isinstance(source_height, int)
    ):
        raise FaceQualityResultError("invalid accepted preview geometry")
    return {
        **expected,
        "scale_x": source_width / pixel_width,
        "scale_y": source_height / pixel_height,
    }


def _validate_quality_configuration(configuration: object) -> QualityFaceConfiguration:
    if not isinstance(configuration, dict):
        raise FaceQualityResultError("invalid processor configuration")
    face_configuration = configuration.get("face_embedding")
    if (
        not isinstance(face_configuration, dict)
        or set(face_configuration) != _FACE_CONFIGURATION_FIELDS
    ):
        raise FaceQualityResultError("invalid processor configuration")
    if (
        face_configuration["model"] != "sface"
        or face_configuration["normalize_embeddings"] is not True
    ):
        raise FaceQualityResultError("invalid processor configuration")
    maximum_faces = face_configuration.get("max_faces")
    if (
        not isinstance(maximum_faces, int)
        or isinstance(maximum_faces, bool)
        or maximum_faces < 1
        or maximum_faces > MAX_QUALITY_FACES
    ):
        raise FaceQualityResultError("invalid processor configuration")
    _probability(face_configuration["detection_threshold"], "invalid processor configuration")
    quality = face_configuration["quality"]
    if not isinstance(quality, dict) or set(quality) != _QUALITY_CONFIGURATION_FIELDS:
        raise FaceQualityResultError("invalid processor configuration")
    if quality["algorithm_version"] != "normalized-laplacian-v1" or quality["crop_size"] != 112:
        raise FaceQualityResultError("invalid processor configuration")
    minimum_face_px = quality["minimum_face_px"]
    if not _positive_int(minimum_face_px) or minimum_face_px > 100_000_000:
        raise FaceQualityResultError("invalid processor configuration")
    severe_blur_threshold = _bounded_number(
        quality["severe_blur_threshold"],
        minimum=0.0,
        maximum=MAX_SHARPNESS,
        message="invalid processor configuration",
    )
    borderline_blur_threshold = _bounded_number(
        quality["borderline_blur_threshold"],
        minimum=0.0,
        maximum=MAX_SHARPNESS,
        message="invalid processor configuration",
    )
    if severe_blur_threshold >= borderline_blur_threshold:
        raise FaceQualityResultError("invalid processor configuration")
    return QualityFaceConfiguration(
        maximum_faces=maximum_faces,
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        minimum_face_px=minimum_face_px,
        severe_blur_threshold=severe_blur_threshold,
        borderline_blur_threshold=borderline_blur_threshold,
        minimum_relative_area=_probability(
            quality["minimum_relative_area"], "invalid processor configuration"
        ),
        minimum_confidence=_probability(
            quality["minimum_confidence"], "invalid processor configuration"
        ),
    )


def _validate_face(
    value: object, *, configuration: QualityFaceConfiguration
) -> ValidatedQualityFace:
    if not isinstance(value, dict) or not _FACE_FIELDS.issubset(value):
        raise FaceQualityResultError("invalid face fields")
    index = value["index"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise FaceQualityResultError("invalid face index")
    bbox = _validate_bbox(value["bbox"])
    confidence = _probability(value["confidence"], "invalid confidence")
    landmarks = _validate_landmarks(value["landmarks"])
    status = value["status"]

    quality: dict[str, Any] | None = None
    embedding: list[float] | None = None
    error_code: str | None = None
    if status == "kept" and set(value) == _FACE_FIELDS | {"quality", "embedding"}:
        quality = _validate_quality(
            value["quality"], expected_decision="accepted", configuration=configuration
        )
        embedding = _validate_embedding(value["embedding"])
    elif status == "quality_rejected" and set(value) == _FACE_FIELDS | {"quality"}:
        quality = _validate_quality(
            value["quality"],
            expected_decision="quality_rejected",
            configuration=configuration,
        )
    elif status == "technical_failed" and set(value) == _FACE_FIELDS | {"error_code"}:
        error_code = value["error_code"]
        if error_code != "invalid_face_quality":
            raise FaceQualityResultError("invalid technical failure")
    elif status == "technical_failed" and set(value) == _FACE_FIELDS | {"quality", "error_code"}:
        quality = _validate_quality(
            value["quality"], expected_decision="accepted", configuration=configuration
        )
        error_code = value["error_code"]
        if error_code != "model_inference_error":
            raise FaceQualityResultError("invalid technical failure")
    else:
        raise FaceQualityResultError("contradictory face state")

    if quality is not None:
        if quality["confidence"] != confidence or not math.isclose(
            quality["minimum_side_px"], min(bbox[2], bbox[3]), rel_tol=0.0, abs_tol=1e-6
        ):
            raise FaceQualityResultError("quality evidence disagrees with detection")
    return ValidatedQualityFace(
        index=index,
        bbox=bbox,
        confidence=confidence,
        landmarks=landmarks,
        status=status,
        quality=quality,
        embedding=embedding,
        error_code=error_code,
    )


def _validate_quality(
    value: object,
    *,
    expected_decision: str,
    configuration: QualityFaceConfiguration,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _QUALITY_FIELDS:
        raise FaceQualityResultError("invalid quality fields")
    if (
        value["algorithm_version"] != configuration.algorithm_version
        or value["crop_size"] != configuration.crop_size
    ):
        raise FaceQualityResultError("invalid quality identity")
    confidence = _probability(value["confidence"], "invalid quality confidence")
    minimum_side = _bounded_number(value["minimum_side_px"], minimum=0.0, maximum=100_000_000.0)
    if minimum_side <= 0.0:
        raise FaceQualityResultError("invalid minimum face side")
    relative_area = _probability(value["relative_area"], "invalid relative area")
    sharpness = _bounded_number(value["sharpness"], minimum=0.0, maximum=MAX_SHARPNESS)
    decision = value["decision"]
    reasons = value["reasons"]
    if not isinstance(reasons, list):
        raise FaceQualityResultError("invalid quality decision")
    reason_sequence = tuple(reasons)
    expected_reasons = _quality_rejection_reasons(
        confidence=confidence,
        minimum_side_px=minimum_side,
        relative_area=relative_area,
        sharpness=sharpness,
        configuration=configuration,
    )
    derived_decision = "quality_rejected" if expected_reasons else "accepted"
    if decision != derived_decision or reason_sequence != expected_reasons:
        raise FaceQualityResultError("invalid quality reasons")
    if decision != expected_decision:
        raise FaceQualityResultError("contradictory face state")
    return {
        "algorithm_version": "normalized-laplacian-v1",
        "crop_size": 112,
        "confidence": confidence,
        "minimum_side_px": minimum_side,
        "relative_area": relative_area,
        "sharpness": sharpness,
        "decision": decision,
        "reasons": list(reasons),
    }


def _quality_rejection_reasons(
    *,
    confidence: float,
    minimum_side_px: float,
    relative_area: float,
    sharpness: float,
    configuration: QualityFaceConfiguration,
) -> tuple[str, ...]:
    """Apply the frozen recall-first decision table to bounded measurements."""
    reasons: list[str] = []
    if minimum_side_px < configuration.minimum_face_px:
        reasons.append("too_small")
    if sharpness < configuration.severe_blur_threshold:
        reasons.append("severe_blur")
    if not reasons and sharpness < configuration.borderline_blur_threshold:
        supporting_reasons: list[str] = []
        if relative_area < configuration.minimum_relative_area:
            supporting_reasons.append("small_relative_area")
        if confidence < configuration.minimum_confidence:
            supporting_reasons.append("low_confidence")
        if supporting_reasons:
            reasons.extend(("borderline_blur", *supporting_reasons))
    return tuple(reasons)


def _validate_embedding(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != EMBEDDING_DIMENSIONS:
        raise FaceQualityResultError("invalid embedding dimensions")
    embedding = [_finite_number(item, "invalid embedding") for item in value]
    norm = math.sqrt(sum(item * item for item in embedding))
    if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        raise FaceQualityResultError("embedding is not normalized")
    return embedding


def _validate_bbox(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise FaceQualityResultError("invalid bounding box")
    bbox = [_bounded_number(item, minimum=0.0, maximum=10_000.0) for item in value]
    if bbox[2] <= 0.0 or bbox[3] <= 0.0:
        raise FaceQualityResultError("invalid bounding box")
    return bbox


def _validate_landmarks(value: object) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 5:
        raise FaceQualityResultError("invalid landmarks")
    landmarks: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise FaceQualityResultError("invalid landmarks")
        landmarks.append([_bounded_number(item, minimum=0.0, maximum=10_000.0) for item in point])
    return landmarks


def _validate_timings(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != _TIMING_FIELDS:
        raise FaceQualityResultError("invalid timings")
    if not all(
        isinstance(duration, int) and not isinstance(duration, bool) and 0 <= duration <= 86_400_000
        for duration in value.values()
    ):
        raise FaceQualityResultError("invalid timings")
    timings = {name: int(value[name]) for name in _TIMING_FIELDS}
    if timings["total_ms"] < sum(timings[name] for name in _TIMING_FIELDS if name != "total_ms"):
        raise FaceQualityResultError("invalid total timing")
    return timings


def _probability(value: object, message: str) -> float:
    return _bounded_number(value, minimum=0.0, maximum=1.0, message=message)


def _bounded_number(
    value: object, *, minimum: float, maximum: float, message: str = "invalid number"
) -> float:
    number = _finite_number(value, message)
    if not minimum <= number <= maximum:
        raise FaceQualityResultError(message)
    return number


def _finite_number(value: object, message: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise FaceQualityResultError(message)
    return float(value)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def publish_face_embedding_projection(attempt: ProcessingAttempt) -> None:
    """Publish or replace one photo's accepted attempt for this exact generation."""
    from processing.models import (  # noqa: PLC0415
        FACE_EMBEDDING_PROCESSOR,
        FaceProcessingAttemptArtifact,
        PhotoFaceEmbeddingProjection,
        ProcessingAttempt,
    )

    if (
        attempt.processor_type != FACE_EMBEDDING_PROCESSOR
        or attempt.status != ProcessingAttempt.Status.SUCCEEDED
        or not attempt.accepted
        or not FaceProcessingAttemptArtifact.objects.filter(
            attempt=attempt,
            status=FaceProcessingAttemptArtifact.Status.COMPLETE,
        ).exists()
    ):
        raise ValueError("face projection requires complete accepted face evidence")
    PhotoFaceEmbeddingProjection.objects.update_or_create(
        photo_id=attempt.photo_id,
        contract_version=attempt.contract_version,
        processor_version=attempt.processor_version,
        configuration_hash=attempt.job.configuration_hash,
        defaults={"accepted_attempt": attempt},
    )


def historical_baseline_face_embedding_generations() -> tuple[dict[str, object], ...]:
    """Return the original v1/v2 baseline accepted only for existing activation rows."""
    from processing.models import FACE_EMBEDDING_PROCESSOR  # noqa: PLC0415
    from processing.services.enrollment import (  # noqa: PLC0415
        CONTRACT_VERSION,
        FACE_EMBEDDING_CONFIGURATION,
        FACE_EMBEDDING_PROCESSOR_VERSION,
        PREVIEW_CONTRACT_VERSION,
    )

    configuration_hash = _canonical_hash(FACE_EMBEDDING_CONFIGURATION)
    return tuple(
        {
            "contract_version": contract_version,
            "processor_type": FACE_EMBEDDING_PROCESSOR,
            "processor_version": processor_version,
            "configuration": deepcopy(FACE_EMBEDDING_CONFIGURATION),
            "configuration_hash": configuration_hash,
            "model": "sface",
        }
        for contract_version, processor_version in (
            (CONTRACT_VERSION, FACE_EMBEDDING_PROCESSOR_VERSION),
            (PREVIEW_CONTRACT_VERSION, 2),
        )
    )


def baseline_face_embedding_generations() -> tuple[dict[str, object], ...]:
    """Return the current baseline set for event-scoped gallery reads and new activations."""
    from processing.models import FACE_EMBEDDING_PROCESSOR  # noqa: PLC0415
    from processing.services.enrollment import (  # noqa: PLC0415
        PREVIEW_CONTRACT_VERSION,
        PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
        SCRFD_FACE_EMBEDDING_CONFIGURATION,
    )

    return historical_baseline_face_embedding_generations() + (
        {
            "contract_version": PREVIEW_CONTRACT_VERSION,
            "processor_type": FACE_EMBEDDING_PROCESSOR,
            "processor_version": PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
            "configuration": deepcopy(SCRFD_FACE_EMBEDDING_CONFIGURATION),
            "configuration_hash": _canonical_hash(SCRFD_FACE_EMBEDDING_CONFIGURATION),
            "model": "sface",
        },
    )


def candidate_face_embedding_generations() -> tuple[dict[str, object], ...]:
    """Return the current preview-backed quality-v4 candidate identity."""
    from processing.models import FACE_EMBEDDING_PROCESSOR  # noqa: PLC0415
    from processing.services.enrollment import (  # noqa: PLC0415
        FACE_EMBEDDING_QUALITY_CONFIGURATION,
        QUALITY_FACE_CONTRACT_VERSION,
        QUALITY_FACE_PROCESSOR_VERSION,
    )

    return (
        {
            "contract_version": QUALITY_FACE_CONTRACT_VERSION,
            "processor_type": FACE_EMBEDDING_PROCESSOR,
            "processor_version": QUALITY_FACE_PROCESSOR_VERSION,
            "configuration": deepcopy(FACE_EMBEDDING_QUALITY_CONFIGURATION),
            "configuration_hash": _canonical_hash(FACE_EMBEDDING_QUALITY_CONFIGURATION),
            "model": "sface",
        },
    )


def historical_quality_face_embedding_generations() -> tuple[dict[str, object], ...]:
    """Return the preserved quality-v3 generation selectable for exact rollback."""
    from processing.models import FACE_EMBEDDING_PROCESSOR  # noqa: PLC0415
    from processing.services.enrollment import (  # noqa: PLC0415
        FACE_EMBEDDING_QUALITY_CONFIGURATION,
        HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION,
        QUALITY_FACE_CONTRACT_VERSION,
    )

    return (
        {
            "contract_version": QUALITY_FACE_CONTRACT_VERSION,
            "processor_type": FACE_EMBEDDING_PROCESSOR,
            "processor_version": HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION,
            "configuration": deepcopy(FACE_EMBEDDING_QUALITY_CONFIGURATION),
            "configuration_hash": _canonical_hash(FACE_EMBEDDING_QUALITY_CONFIGURATION),
            "model": "sface",
        },
    )


def candidate_face_embedding_status(event: Event) -> dict[str, object]:
    """Return privacy-safe exact v4 candidate processing and projection aggregates."""
    from processing.models import (  # noqa: PLC0415
        FACE_EMBEDDING_PROCESSOR,
        PhotoFaceDetection,
        PhotoFaceEmbeddingProjection,
        PhotoProcessingState,
        ProcessingAttempt,
        ProcessingJob,
    )
    from processing.services.enrollment import (  # noqa: PLC0415
        QUALITY_FACE_CONTRACT_VERSION,
        QUALITY_FACE_PROCESSOR_VERSION,
        candidate_face_embedding_cohort,
    )

    candidate = candidate_face_embedding_generations()[0]
    jobs = ProcessingJob.objects.filter(
        event=event,
        contract_version=QUALITY_FACE_CONTRACT_VERSION,
        processor_type=FACE_EMBEDDING_PROCESSOR,
        processor_version=QUALITY_FACE_PROCESSOR_VERSION,
        configuration_hash=candidate["configuration_hash"],
    )
    attempts = ProcessingAttempt.objects.filter(job__in=jobs)
    states = PhotoProcessingState.objects.filter(
        processor_type=FACE_EMBEDDING_PROCESSOR,
        current_job__in=jobs,
    )
    projections = PhotoFaceEmbeddingProjection.objects.filter(
        photo__event=event,
        contract_version=QUALITY_FACE_CONTRACT_VERSION,
        processor_version=QUALITY_FACE_PROCESSOR_VERSION,
        configuration_hash=candidate["configuration_hash"],
    )
    detections = PhotoFaceDetection.objects.filter(attempt__job__in=jobs)

    def status_counts(queryset, statuses: tuple[str, ...]) -> dict[str, int]:
        counts = {status: 0 for status in statuses}
        for row in queryset.values("status").annotate(count=Count("id")):
            counts[row["status"]] = row["count"]
        return counts

    job_statuses = status_counts(jobs, tuple(str(value) for value in ProcessingJob.Status.values))
    attempt_statuses = status_counts(
        attempts, tuple(str(value) for value in ProcessingAttempt.Status.values)
    )
    state_statuses = status_counts(
        states, tuple(str(value) for value in PhotoProcessingState.Status.values)
    )
    detection_statuses = status_counts(
        detections, tuple(str(value) for value in PhotoFaceDetection.Status.values)
    )
    succeeded_job_status = str(ProcessingJob.Status.SUCCEEDED)
    failed_job_status = str(ProcessingJob.Status.FAILED)
    cancelled_job_status = str(ProcessingJob.Status.CANCELLED)
    terminal_job_count = (
        job_statuses[succeeded_job_status]
        + job_statuses[failed_job_status]
        + job_statuses[cancelled_job_status]
    )
    failure_job_count = job_statuses[failed_job_status] + job_statuses[cancelled_job_status]
    failure_attempt_count = (
        attempt_statuses[str(ProcessingAttempt.Status.FAILED)]
        + attempt_statuses[str(ProcessingAttempt.Status.EXPIRED)]
        + attempt_statuses[str(ProcessingAttempt.Status.STALE)]
    )
    return {
        "accepted_attempt_count": attempts.filter(
            status=ProcessingAttempt.Status.SUCCEEDED, accepted=True
        ).count(),
        "candidate_attempt_count": attempts.count(),
        "candidate_attempt_status_counts": attempt_statuses,
        "candidate_job_count": jobs.count(),
        "candidate_job_status_counts": job_statuses,
        "candidate_projection_count": projections.count(),
        "candidate_state_counts": state_statuses,
        "candidate_face_detection_status_counts": detection_statuses,
        "eligible_photo_count": len(candidate_face_embedding_cohort(event)),
        "failure_attempt_count": failure_attempt_count,
        "failure_job_count": failure_job_count,
        "nonterminal_job_count": jobs.count() - terminal_job_count,
        "kept_face_count": detection_statuses[str(PhotoFaceDetection.Status.KEPT)],
        "quality_rejected_face_count": detection_statuses[
            str(PhotoFaceDetection.Status.QUALITY_REJECTED)
        ],
        "terminal_job_count": terminal_job_count,
        "technical_failure_face_count": detection_statuses[str(PhotoFaceDetection.Status.FAILED)],
        "unexpected_attempt_count": attempts.exclude(
            Q(status=ProcessingAttempt.Status.SUCCEEDED, accepted=True)
            | Q(
                status__in=(
                    ProcessingAttempt.Status.FAILED,
                    ProcessingAttempt.Status.EXPIRED,
                    ProcessingAttempt.Status.STALE,
                ),
                accepted=False,
            )
        ).count(),
    }


def active_face_embedding_generations(event: Event) -> tuple[dict[str, object], ...]:
    """Resolve one event's latest explicit selection, or its initial frozen baseline."""
    from processing.models import EventFaceEmbeddingActivation  # noqa: PLC0415

    activation = (
        EventFaceEmbeddingActivation.objects.filter(event=event)
        .order_by("-activated_at", "-id")
        .first()
    )
    if activation is None:
        return baseline_face_embedding_generations()
    generations = validate_face_embedding_generations(activation.generations)
    if activation.generation_set_hash != _canonical_hash(list(generations)):
        raise ValueError("invalid face-embedding activation record")
    if generations in (
        historical_baseline_face_embedding_generations(),
        baseline_face_embedding_generations(),
    ):
        if activation.approved_configuration_hash or activation.approved_evaluation_report_hash:
            raise ValueError("baseline activation must not claim candidate approval")
    elif generations == candidate_face_embedding_generations():
        _validate_candidate_activation(
            event=event,
            approved_configuration_hash=activation.approved_configuration_hash,
            evaluation_report_hash=activation.approved_evaluation_report_hash,
        )
    return generations


def activate_face_embedding_generation(
    *,
    event: Event,
    generations: Sequence[Mapping[str, object]],
    approved_configuration_hash: str,
    evaluation_report_hash: str,
    review_confirmed: bool,
):
    """Append one guarded event selection, returning the latest row on exact replay."""
    from processing.models import EventFaceEmbeddingActivation  # noqa: PLC0415

    if review_confirmed is not True:
        raise ValueError("review confirmation is required")
    selected = validate_face_embedding_generations(generations)
    baseline = baseline_face_embedding_generations()
    candidate = candidate_face_embedding_generations()
    historical = historical_quality_face_embedding_generations()
    if selected == baseline:
        if approved_configuration_hash or evaluation_report_hash:
            raise ValueError("baseline activation must not claim candidate approval")
    elif selected == candidate:
        pass
    elif selected != historical:  # pragma: no cover - generation-set validation rejects this.
        raise ValueError("unrecognized face-embedding generation set")

    serialized_generations = [deepcopy(generation) for generation in selected]
    generation_set_hash = _canonical_hash(serialized_generations)
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        if selected == candidate:
            _validate_candidate_activation(
                event=locked_event,
                approved_configuration_hash=approved_configuration_hash,
                evaluation_report_hash=evaluation_report_hash,
            )
        latest = (
            EventFaceEmbeddingActivation.objects.select_for_update()
            .filter(event=locked_event)
            .order_by("-activated_at", "-id")
            .first()
        )
        if latest is not None and (
            latest.generations == serialized_generations
            and latest.generation_set_hash == generation_set_hash
            and latest.approved_configuration_hash == approved_configuration_hash
            and latest.approved_evaluation_report_hash == evaluation_report_hash
        ):
            return latest
        return EventFaceEmbeddingActivation.objects.create(
            event=locked_event,
            generations=serialized_generations,
            generation_set_hash=generation_set_hash,
            approved_configuration_hash=approved_configuration_hash,
            approved_evaluation_report_hash=evaluation_report_hash,
        )


def validate_face_embedding_generations(
    generations: Sequence[Mapping[str, object]] | object,
) -> tuple[dict[str, object], ...]:
    if not isinstance(generations, (list, tuple)):
        raise ValueError("invalid face-embedding generation set")
    normalized = tuple(
        dict(generation) for generation in generations if isinstance(generation, Mapping)
    )
    if len(normalized) != len(generations):
        raise ValueError("invalid face-embedding generation set")
    if normalized not in (
        historical_baseline_face_embedding_generations(),
        baseline_face_embedding_generations(),
        historical_quality_face_embedding_generations(),
        candidate_face_embedding_generations(),
    ):
        raise ValueError("invalid face-embedding generation set")
    return tuple(deepcopy(generation) for generation in normalized)


def _validate_candidate_activation(
    *, event: Event, approved_configuration_hash: str, evaluation_report_hash: str
) -> None:
    from processing.models import PhotoFaceEmbeddingProjection  # noqa: PLC0415
    from processing.services import enrollment  # noqa: PLC0415

    approval = enrollment.FACE_EMBEDDING_QUALITY_APPROVAL
    candidate = candidate_face_embedding_generations()[0]
    if (
        approval is None
        or approval.approved is not True
        or approval.event_slug != event.slug
        or approval.configuration_hash != candidate["configuration_hash"]
        or approval.configuration_hash != approved_configuration_hash
        or approval.comparison_manifest_hash != evaluation_report_hash
        or not _is_sha256(approval.configuration_hash)
        or not _is_sha256(approval.preview_manifest_hash)
        or not _is_sha256(approval.comparison_manifest_hash)
        or not _is_sha256(approval.yunet_model_hash)
        or not _is_sha256(approval.sface_model_hash)
        or approval.technical_failure_count != 0
    ):
        raise ValueError("candidate activation requires approved benchmark evidence")

    status = candidate_face_embedding_status(event)
    if enrollment.accepted_preview_cohort_hash(event) != approval.accepted_preview_cohort_hash:
        raise ValueError("candidate activation requires approved accepted preview cohort")
    eligible_photo_ids = {photo.pk for photo in enrollment.candidate_face_embedding_cohort(event)}
    projected_photo_ids = set(
        PhotoFaceEmbeddingProjection.objects.filter(
            photo__event=event,
            contract_version=candidate["contract_version"],
            processor_version=candidate["processor_version"],
            configuration_hash=candidate["configuration_hash"],
            accepted_attempt__job__contract_version=candidate["contract_version"],
            accepted_attempt__job__processor_version=candidate["processor_version"],
            accepted_attempt__job__configuration_hash=candidate["configuration_hash"],
            accepted_attempt__status="succeeded",
            accepted_attempt__accepted=True,
        ).values_list("photo_id", flat=True)
    )
    if (
        approval.photo_count != len(eligible_photo_ids)
        or not eligible_photo_ids
        or len(
            {
                approval.photo_count,
                approval.job_count,
                approval.attempt_count,
                approval.projection_count,
            }
        )
        != 1
        or approval.job_count != status["candidate_job_count"]
        or approval.projection_count != status["candidate_projection_count"]
        or status["terminal_job_count"] != approval.job_count
        or status["nonterminal_job_count"] != 0
        or status["failure_job_count"] != 0
        or status["accepted_attempt_count"] != approval.attempt_count
        or status["unexpected_attempt_count"] != 0
        or status["kept_face_count"] != approval.kept_face_count
        or status["quality_rejected_face_count"] != approval.quality_rejected_face_count
        or status["technical_failure_face_count"] != 0
        or projected_photo_ids != eligible_photo_ids
    ):
        raise ValueError("incomplete candidate evidence")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
