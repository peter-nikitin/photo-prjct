from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from picflow.models import Event
from processing.models import FACE_EMBEDDING_PROCESSOR, FaceEmbedding, PhotoProcessingState
from processing.services.enrollment import (
    CONTRACT_VERSION as FACE_EMBEDDING_CONTRACT_VERSION,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
    PREVIEW_CONTRACT_VERSION,
    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
)

from selfie_search.models import SelfieSearch, SelfieSearchCandidate, SelfieSearchJob


@dataclass(frozen=True)
class CreatedSearch:
    search: SelfieSearch
    public_token: str


def submit_selfie_search(*, event: Event, upload, storage) -> CreatedSearch:
    """Persist one validated selfie submission and its immutable current event cohort."""
    try:
        event = Event.objects.get(pk=event.pk, publication_status=Event.PublicationStatus.PUBLISHED)
    except Event.DoesNotExist:
        raise ValueError("selfie search requires a published event") from None

    content = upload.read()
    upload.seek(0)
    content_type = upload.content_type
    key = f"selfie-search/{uuid4().hex}"
    stored = storage.put(key=key, content=content, content_type=content_type)
    public_token = secrets.token_urlsafe(32)
    configuration = _configuration(content_type=content_type, content_size=len(content))
    try:
        with transaction.atomic():
            search = SelfieSearch.objects.create(
                event=event,
                public_token_digest=_token_digest(public_token),
                temporary_object_key=stored.key,
                configuration=configuration,
                configuration_hash=_configuration_hash(configuration),
            )
            candidates = _compatible_candidates(event=event, configuration=configuration)
            SelfieSearchCandidate.objects.bulk_create(
                [
                    SelfieSearchCandidate(search=search, embedding=embedding, photo_id=photo_id)
                    for embedding, photo_id in candidates
                ]
            )
            photo_count = len({photo_id for _, photo_id in candidates})
            search.eligible_photo_count = photo_count
            search.eligible_face_count = len(candidates)
            search.save(update_fields=["eligible_photo_count", "eligible_face_count"])
            SelfieSearchJob.objects.create(search=search, configuration=configuration)
    except Exception:
        try:
            storage.delete(key=stored.key)
        except Exception:
            pass
        raise
    return CreatedSearch(search=search, public_token=public_token)


def resolve_public_search(event_slug: str, public_token: str) -> SelfieSearch:
    return SelfieSearch.objects.get(
        event__slug=event_slug,
        public_token_digest=_token_digest(public_token),
    )


def _configuration(*, content_type: str, content_size: int) -> dict[str, object]:
    gallery_generations = _face_embedding_generations()
    gallery_model = gallery_generations[0]["model"]
    if not isinstance(gallery_model, str):
        raise ValueError("invalid face-embedding generation")
    return {
        "contract_version": 1,
        "processor": "selfie_query",
        "embedding_model": gallery_model,
        "embedding_dimensions": settings.SELFIE_SEARCH_EMBEDDING_DIMENSIONS,
        "cosine_distance_threshold": settings.SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD,
        "content_type": content_type,
        "content_size": content_size,
        "gallery_face_embedding_generations": list(gallery_generations),
    }


def _compatible_candidates(*, event: Event, configuration: dict[str, object]):
    configured_generations = configuration.get("gallery_face_embedding_generations")
    if not isinstance(configured_generations, list) or not all(
        isinstance(generation, dict) for generation in configured_generations
    ):
        raise ValueError("invalid face-embedding generation")
    generations = tuple(configured_generations)
    if not generations:
        raise ValueError("invalid face-embedding generation")

    compatible_generation = Q()
    for generation in generations:
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
    embeddings = (
        FaceEmbedding.objects.filter(
            detection__status="kept",
            detection__attempt__event=event,
            detection__attempt__status="succeeded",
            detection__attempt__accepted=True,
            detection__attempt__accepted_states__processor_type=FACE_EMBEDDING_PROCESSOR,
            detection__attempt__accepted_states__status=PhotoProcessingState.Status.SUCCEEDED,
            detection__attempt__photo__event=event,
            detection__attempt__photo__src="",
            detection__attempt__photo__original_key__isnull=False,
            detection__attempt__photo__original_key__gt="",
            detection__attempt__photo__original_size__isnull=False,
        )
        .filter(compatible_generation)
        .select_related("detection__attempt__photo")
    )
    dimensions = configuration["embedding_dimensions"]
    return [
        (embedding, embedding.detection.attempt.photo_id)
        for embedding in embeddings
        if isinstance(embedding.vector, list) and len(embedding.vector) == dimensions
    ]


def _face_embedding_generations() -> tuple[dict[str, object], ...]:
    return (
        _face_embedding_generation(
            contract_version=FACE_EMBEDDING_CONTRACT_VERSION,
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
        ),
        _face_embedding_generation(
            contract_version=PREVIEW_CONTRACT_VERSION,
            processor_version=PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
        ),
    )


def _face_embedding_generation(
    *, contract_version: int, processor_version: int
) -> dict[str, object]:
    face_configuration = FACE_EMBEDDING_CONFIGURATION.get("face_embedding")
    if not isinstance(face_configuration, dict):
        raise ValueError("invalid current face-embedding configuration")
    model = face_configuration.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("invalid current face-embedding model")
    return {
        "contract_version": contract_version,
        "processor_type": FACE_EMBEDDING_PROCESSOR,
        "processor_version": processor_version,
        "configuration": FACE_EMBEDDING_CONFIGURATION,
        "configuration_hash": _configuration_hash(FACE_EMBEDDING_CONFIGURATION),
        "model": model,
    }


def _token_digest(public_token: str) -> str:
    return hashlib.sha256(public_token.encode("ascii")).hexdigest()


def _configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
