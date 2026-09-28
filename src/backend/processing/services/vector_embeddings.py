"""Validated atomic publication in the JSON and vector evidence stores."""

import math
from collections.abc import Iterable
from typing import Any

from django.db import transaction

from processing.models import (
    FaceEmbedding,
    FaceEmbeddingVector,
    PhotoFaceDetection,
    ProcessingAttempt,
)

MODEL_DIMENSIONS = {"sface": 128, "adaface-ir18-webface4m": 512}


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
    if (
        detection.status != PhotoFaceDetection.Status.KEPT
        or detection.attempt.status != ProcessingAttempt.Status.SUCCEEDED
        or not detection.attempt.accepted
    ):
        raise ValueError("embedding requires an accepted kept face")
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
