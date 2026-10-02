"""Validated immutable face publication with a pinned vector-only candidate contract."""

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

from django.db import transaction

from processing.models import (
    FaceEmbedding,
    FaceEmbeddingVector,
    PhotoFaceDetection,
    ProcessingAttempt,
)

MODEL_DIMENSIONS = {"sface": 128, "adaface-ir18-webface4m": 512}


def generation_uses_vector_only_storage(generation: Mapping[str, object]) -> bool:
    """Recognize only the approved immutable historical AdaFace publication identity."""
    configuration = generation.get("configuration")
    if not isinstance(configuration, dict) or "embedding_storage" not in configuration:
        return False
    from processing.services.enrollment import LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION

    expected = {**LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION, "embedding_storage": "vector_only"}
    configuration_hash = hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        generation.get("contract_version") != 3
        or generation.get("processor_type") != "face_embedding"
        or generation.get("processor_version") != 5
        or generation.get("model") != "adaface-ir18-webface4m"
        or configuration != expected
        or generation.get("configuration_hash") != configuration_hash
    ):
        raise ValueError("unsupported vector-only embedding generation")
    return True


def _attempt_uses_vector_only_storage(attempt: ProcessingAttempt, model_version: str) -> bool:
    if not generation_uses_vector_only_storage(
        {
            "contract_version": attempt.contract_version,
            "processor_type": attempt.processor_type,
            "processor_version": attempt.processor_version,
            "configuration": attempt.configuration,
            "configuration_hash": attempt.job.configuration_hash,
            "model": model_version,
        }
    ):
        return False
    for source in (attempt.job, attempt.run):
        if (
            source.contract_version != attempt.contract_version
            or source.processor_type != attempt.processor_type
            or source.processor_version != attempt.processor_version
            or source.configuration != attempt.configuration
            or source.configuration_hash != attempt.job.configuration_hash
            or source.event_id != attempt.event_id
        ):
            raise ValueError("vector-only attempt generation mismatch")
    return True


@transaction.atomic
def persist_accepted_embedding(
    *,
    detection: PhotoFaceDetection,
    model_version: str,
    vector: list[float],
    metadata: dict[str, Any],
) -> FaceEmbedding | FaceEmbeddingVector:
    """Choose publication from accepted attempt identity, never mutable event selection."""
    if not _attempt_uses_vector_only_storage(detection.attempt, model_version):
        return persist_parallel_embedding(
            detection=detection, model_version=model_version, vector=vector, metadata=metadata
        )
    values = validate_embedding(vector, model_version=model_version)
    _require_accepted_kept_face(detection)
    return FaceEmbeddingVector.objects.create(
        detection=detection,
        model_version=model_version,
        vector=values,
        metadata=non_vector_metadata(metadata),
    )


def vector_values(value: Iterable[float]) -> list[float]:
    """Make driver-returned components explicit Python floats at the boundary."""
    return [float(component) for component in value]


def validate_embedding(value: Iterable[float], *, model_version: str) -> list[float]:
    dimensions = MODEL_DIMENSIONS.get(model_version)
    if dimensions is None:
        raise ValueError("unsupported embedding model")
    try:
        components = list(value)
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in components):
            # VectorField returns NumPy float32 values; convert them only at the read boundary.
            raise ValueError("embedding components must be real numbers")
        values = vector_values(components)
    except (TypeError, OverflowError) as exc:
        raise ValueError("invalid embedding components") from exc
    if len(values) != dimensions or not all(math.isfinite(item) for item in values):
        raise ValueError("invalid embedding dimensions or components")
    norm = math.sqrt(math.fsum(item * item for item in values))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("embedding is not normalized")
    return values


def non_vector_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Retain evidence metadata without copying the legacy embedding payload."""
    return {key: value for key, value in metadata.items() if key != "embedding"}


@transaction.atomic
def persist_parallel_embedding(
    *,
    detection: PhotoFaceDetection,
    model_version: str,
    vector: list[float],
    metadata: dict[str, Any],
) -> FaceEmbedding:
    values = validate_embedding(vector, model_version=model_version)
    _require_accepted_kept_face(detection)
    legacy = FaceEmbedding.objects.create(
        detection=detection, model_version=model_version, vector=values, metadata=metadata
    )
    FaceEmbeddingVector.objects.create(
        detection=detection,
        model_version=model_version,
        vector=values,
        metadata=non_vector_metadata(metadata),
    )
    return legacy


def _require_accepted_kept_face(detection: PhotoFaceDetection) -> None:
    if (
        detection.status != PhotoFaceDetection.Status.KEPT
        or detection.attempt.status != ProcessingAttempt.Status.SUCCEEDED
        or not detection.attempt.accepted
    ):
        raise ValueError("embedding requires an accepted kept face")
