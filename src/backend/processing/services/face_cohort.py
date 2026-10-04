"""Shared eligibility and lightweight value types for gallery face cohorts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import F, Q, QuerySet
from picflow.models import Event

from processing.models import PhotoFaceDetection, PhotoFaceEmbeddingProjection


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
    from processing.services.vector_embeddings import validate_embedding, vector_values

    generation_by_identity = {
        (item["contract_version"], item["processor_version"], item["configuration_hash"]): item
        for item in generations
    }
    rows: list[CompatibleFaceEmbedding] = []
    for row in (
        eligible_face_detections(event, generations)
        .values(
            "id",
            "embedding_vector__id",
            "embedding_vector__vector",
            "embedding_vector__model_version",
            "attempt_id",
            "attempt__photo_id",
            "attempt__event_id",
            "attempt__photo__event_id",
            "attempt__contract_version",
            "attempt__processor_version",
            "attempt__job__configuration_hash",
        )
        .iterator(chunk_size=2000)
    ):
        generation = generation_by_identity[
            (
                row["attempt__contract_version"],
                row["attempt__processor_version"],
                row["attempt__job__configuration_hash"],
            )
        ]
        model = str(generation["model"])
        if row["embedding_vector__id"] is None or row["embedding_vector__model_version"] != model:
            raise ValueError("native cohort vector identity is incomplete")
        values = validate_embedding(
            vector_values(row["embedding_vector__vector"]), model_version=model
        )
        if len(values) != dimensions:
            raise ValueError("native cohort dimensions disagree with generation")
        rows.append(
            CompatibleFaceEmbedding(
                vector=tuple(values),
                model_version=model,
                detection_id=row["id"],
                photo_id=row["attempt__photo_id"],
                photo_event_id=row["attempt__photo__event_id"],
                attempt_event_id=row["attempt__event_id"],
                attempt_photo_id=row["attempt__photo_id"],
                attempt_id=row["attempt_id"],
                contract_version=row["attempt__contract_version"],
                processor_version=row["attempt__processor_version"],
                configuration_hash=row["attempt__job__configuration_hash"],
            )
        )
    return tuple(rows)


def _projection_generation_predicate(
    generations: Sequence[Mapping[str, object]],
) -> Q:
    compatible_generation = Q()
    for generation in generations:
        _validate_generation(generation)
        generation_predicate = Q(
            contract_version=generation["contract_version"],
            processor_version=generation["processor_version"],
            configuration_hash=generation["configuration_hash"],
            accepted_attempt__contract_version=generation["contract_version"],
            accepted_attempt__processor_type=generation["processor_type"],
            accepted_attempt__processor_version=generation["processor_version"],
            accepted_attempt__job__contract_version=generation["contract_version"],
            accepted_attempt__job__processor_type=generation["processor_type"],
            accepted_attempt__job__processor_version=generation["processor_version"],
            accepted_attempt__job__configuration_hash=generation["configuration_hash"],
            accepted_attempt__run__contract_version=generation["contract_version"],
            accepted_attempt__run__processor_type=generation["processor_type"],
            accepted_attempt__run__processor_version=generation["processor_version"],
            accepted_attempt__run__configuration_hash=generation["configuration_hash"],
        )
        compatible_generation |= generation_predicate
    return compatible_generation


def _validate_generation(generation: Mapping[str, object]) -> None:
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
    if any(key not in generation for key in required) or not isinstance(
        generation["configuration_hash"], str
    ):
        raise ValueError("invalid face-embedding generation")


def eligible_face_detections(
    event: Event | None = None,
    generations: Sequence[Mapping[str, object]] | None = None,
    *,
    include_hidden: bool = False,
) -> QuerySet[PhotoFaceDetection]:
    """Scalar current-projection eligibility independent of either embedding store.

    Explicit generations pin online use. Without them, operator reconciliation covers every
    internally consistent current projection, including older accepted generations.
    """
    projections = _scalar_face_projections(event, include_hidden=include_hidden)
    if generations is not None:
        if not generations:
            raise ValueError("face-embedding generations are required")
        projections = projections.filter(
            _projection_generation_predicate(generations),
            photo__processing_states__processor_type="face_embedding",
            photo__processing_states__status="succeeded",
            photo__processing_states__accepted_attempt=F("accepted_attempt"),
            photo__processing_states__current_attempt=F("accepted_attempt"),
            photo__processing_states__current_job=F("accepted_attempt__job"),
            photo__processing_states__current_run=F("accepted_attempt__run"),
        )
    else:
        projections = projections.filter(
            accepted_attempt__processor_type="face_embedding",
            accepted_attempt__contract_version=F("contract_version"),
            accepted_attempt__processor_version=F("processor_version"),
            accepted_attempt__job__processor_type="face_embedding",
            accepted_attempt__job__contract_version=F("contract_version"),
            accepted_attempt__job__processor_version=F("processor_version"),
            accepted_attempt__job__configuration_hash=F("configuration_hash"),
            accepted_attempt__run__processor_type="face_embedding",
            accepted_attempt__run__contract_version=F("contract_version"),
            accepted_attempt__run__processor_version=F("processor_version"),
            accepted_attempt__run__configuration_hash=F("configuration_hash"),
        )
    return PhotoFaceDetection.objects.filter(
        pk__in=projections.values("accepted_attempt__face_detections__id")
    )


def _scalar_face_projections(
    event: Event | None,
    generation_predicate: Q | None = None,
    *,
    include_hidden: bool = False,
) -> QuerySet[PhotoFaceEmbeddingProjection]:
    predicate = Q(
        photo__src="",
        photo__original_key__isnull=False,
        photo__original_key__gt="",
        photo__original_size__isnull=False,
        accepted_attempt__event_id=F("photo__event_id"),
        accepted_attempt__photo_id=F("photo_id"),
        accepted_attempt__status="succeeded",
        accepted_attempt__accepted=True,
        accepted_attempt__face_detections__status="kept",
    )
    if not include_hidden:
        predicate &= Q(photo__is_hidden=False)
    if event is not None:
        predicate &= Q(photo__event=event)
    if generation_predicate is not None:
        predicate &= generation_predicate
    return PhotoFaceEmbeddingProjection.objects.filter(predicate)
