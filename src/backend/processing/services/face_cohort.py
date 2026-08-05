"""Shared eligibility and lightweight value types for gallery face cohorts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import F, Q, QuerySet
from picflow.models import Event

from processing.models import FACE_EMBEDDING_PROCESSOR, FaceEmbedding, PhotoProcessingState


@dataclass(frozen=True, slots=True)
class CompatibleFaceEmbedding:
    """The bounded fields shared by direct ranking and offline clustering."""

    vector: tuple[float, ...]
    model_version: str
    detection_id: UUID
    photo_id: str
    photo_event_id: Any
    attempt_event_id: Any
    attempt_photo_id: str
    attempt_id: UUID | None = None
    contract_version: int | None = None
    processor_version: int | None = None
    configuration_hash: str = ""

    @property
    def event_id(self) -> Any:
        return self.photo_event_id


def load_compatible_face_embeddings(
    event: Event,
    generations: Sequence[Mapping[str, object]],
    dimensions: int,
) -> tuple[CompatibleFaceEmbedding, ...]:
    """Load one deterministic, event-scoped cohort for accepted face embeddings.

    Eligibility is deliberately kept in ``processing`` so direct search and offline corpus
    building cannot drift.  Only bounded identity/vector fields are selected; metadata, geometry,
    and processing payloads stay out of the cohort.
    """
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("embedding dimensions must be positive")
    if not generations:
        raise ValueError("face-embedding generations are required")

    embeddings = compatible_face_embedding_queryset(event, generations)

    rows: list[CompatibleFaceEmbedding] = []
    for (
        vector,
        model_version,
        detection_id,
        photo_id,
        photo_event_id,
        attempt_event_id,
        attempt_id,
        contract_version,
        processor_version,
        configuration_hash,
    ) in embeddings.values_list(
        "vector",
        "model_version",
        "detection_id",
        "detection__attempt__photo_id",
        "detection__attempt__photo__event_id",
        "detection__attempt__event_id",
        "detection__attempt_id",
        "detection__attempt__contract_version",
        "detection__attempt__processor_version",
        "detection__attempt__run__configuration_hash",
    ).iterator(chunk_size=2_000):
        if not isinstance(vector, list) or len(vector) != dimensions:
            continue
        if not isinstance(model_version, str) or not isinstance(detection_id, UUID):
            continue
        if not isinstance(photo_id, str) or not isinstance(attempt_id, UUID):
            continue
        rows.append(
            CompatibleFaceEmbedding(
                vector=tuple(vector),
                model_version=model_version,
                detection_id=detection_id,
                photo_id=photo_id,
                photo_event_id=photo_event_id,
                attempt_event_id=attempt_event_id,
                attempt_photo_id=photo_id,
                attempt_id=attempt_id,
                contract_version=contract_version,
                processor_version=processor_version,
                configuration_hash=(
                    configuration_hash if isinstance(configuration_hash, str) else ""
                ),
            )
        )
    return tuple(rows)


def compatible_face_embedding_queryset(
    event: Event,
    generations: Sequence[Mapping[str, object]],
) -> QuerySet[FaceEmbedding]:
    """Return the shared current accepted cohort before bounded field projection."""
    if not generations:
        raise ValueError("face-embedding generations are required")
    compatible_generation = Q()
    for generation in generations:
        if not isinstance(generation, Mapping):
            raise ValueError("invalid face-embedding generation")
        required = (
            "model",
            "contract_version",
            "processor_type",
            "processor_version",
            "configuration",
            "configuration_hash",
        )
        if any(key not in generation for key in required):
            raise ValueError("invalid face-embedding generation")
        compatible_generation |= Q(
            model_version=generation["model"],
            detection__attempt__contract_version=generation["contract_version"],
            detection__attempt__processor_type=generation["processor_type"],
            detection__attempt__processor_version=generation["processor_version"],
            detection__attempt__configuration=generation["configuration"],
            detection__attempt__job__contract_version=generation["contract_version"],
            detection__attempt__job__processor_type=generation["processor_type"],
            detection__attempt__job__processor_version=generation["processor_version"],
            detection__attempt__job__configuration=generation["configuration"],
            detection__attempt__job__configuration_hash=generation["configuration_hash"],
            detection__attempt__run__contract_version=generation["contract_version"],
            detection__attempt__run__processor_type=generation["processor_type"],
            detection__attempt__run__processor_version=generation["processor_version"],
            detection__attempt__run__configuration=generation["configuration"],
            detection__attempt__run__configuration_hash=generation["configuration_hash"],
        )

    return (
        FaceEmbedding.objects.filter(
            detection__status="kept",
            detection__attempt__event=event,
            detection__attempt__status="succeeded",
            detection__attempt__accepted=True,
            detection__attempt__accepted_states__processor_type=FACE_EMBEDDING_PROCESSOR,
            detection__attempt__accepted_states__status=PhotoProcessingState.Status.SUCCEEDED,
            detection__attempt__accepted_states__accepted_attempt_id=F("detection__attempt_id"),
            detection__attempt__photo__event=event,
            detection__attempt__photo__src="",
            detection__attempt__photo__original_key__isnull=False,
            detection__attempt__photo__original_key__gt="",
            detection__attempt__photo__original_size__isnull=False,
        )
        .filter(compatible_generation)
        .order_by("detection_id")
    )
