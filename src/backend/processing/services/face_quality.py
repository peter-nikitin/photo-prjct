"""Strict validation for quality-gated gallery face terminal results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from picflow.models import Event

if TYPE_CHECKING:
    from processing.models import ProcessingAttempt

QUALITY_FACE_CONTRACT_VERSION = 3
QUALITY_FACE_PROCESSOR_VERSION = 5
QUALITY_FACE_PROCESSOR_VERSIONS = frozenset({QUALITY_FACE_PROCESSOR_VERSION})
MAX_QUALITY_FACES = 64
ADAFACE_EMBEDDING_DIMENSIONS = 512
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
_PUBLISHED_PREVIEW_KEY = re.compile(
    r"derivatives/previews/(?P<photo_id>[A-Za-z0-9_-]{1,32})/preview-small-v1/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-"
    r"[0-9a-f]{64}\.jpg"
)
_FACE_CONFIGURATION_FIELDS = frozenset(
    {
        "model",
        "max_faces",
        "detection_threshold",
        "normalize_embeddings",
        "quality",
        "embedding_dimensions",
    }
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
    model: str
    embedding_dimensions: int
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
    if value["model"] != quality_configuration.model:
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
        model=quality_configuration.model,
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
    if processor_version != QUALITY_FACE_PROCESSOR_VERSION:
        raise FaceQualityResultError("invalid face-processing version")
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
        raise FaceQualityResultError("quality processing requires preview input")
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
    if not isinstance(face_configuration, dict):
        raise FaceQualityResultError("invalid processor configuration")
    model = face_configuration.get("model")
    if model != "adaface-ir18-webface4m":
        raise FaceQualityResultError("invalid processor configuration")
    embedding_dimensions = ADAFACE_EMBEDDING_DIMENSIONS
    if (
        set(face_configuration) != _FACE_CONFIGURATION_FIELDS
        or face_configuration["normalize_embeddings"] is not True
        or face_configuration["embedding_dimensions"] != embedding_dimensions
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
        model=model,
        embedding_dimensions=embedding_dimensions,
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
        embedding = _validate_embedding(
            value["embedding"], dimensions=configuration.embedding_dimensions
        )
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


def _validate_embedding(value: object, *, dimensions: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimensions:
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


def current_face_embedding_generation() -> dict[str, object]:
    """Pinned AdaFace v5 identity for all newly enrolled face work."""
    from processing.models import FACE_EMBEDDING_PROCESSOR  # noqa: PLC0415
    from processing.services.enrollment import (  # noqa: PLC0415
        FACE_EMBEDDING_CONFIGURATION,
        QUALITY_FACE_CONTRACT_VERSION,
        QUALITY_FACE_PROCESSOR_VERSION,
    )

    configuration = deepcopy(FACE_EMBEDDING_CONFIGURATION)
    return {
        "contract_version": QUALITY_FACE_CONTRACT_VERSION,
        "processor_type": FACE_EMBEDDING_PROCESSOR,
        "processor_version": QUALITY_FACE_PROCESSOR_VERSION,
        "configuration": configuration,
        "configuration_hash": _canonical_hash(configuration),
        "model": "adaface-ir18-webface4m",
    }


def active_face_embedding_generations(event: Event) -> tuple[dict[str, object], ...]:
    """Accepted AdaFace v5 identities for current reads, independent of old activations."""
    del event
    current = current_face_embedding_generation()
    historical = deepcopy(current)
    historical_configuration = cast(dict[str, object], historical["configuration"])
    historical_configuration["embedding_storage"] = "vector_only"
    historical["configuration_hash"] = _canonical_hash(historical_configuration)
    return current, historical


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
