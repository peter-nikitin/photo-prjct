from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from picflow.models import Event
from processing.models import FACE_EMBEDDING_PROCESSOR
from processing.services.enrollment import (
    CONTRACT_VERSION as FACE_EMBEDDING_CONTRACT_VERSION,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
    PREVIEW_CONTRACT_VERSION,
    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
)
from processing.services.face_cohort import load_compatible_face_embeddings

from selfie_search.images import PreparedSelfie
from selfie_search.models import SelfieSearch, SelfieSearchJob
from selfie_search.services.ranking import CandidateEmbedding


@dataclass(frozen=True)
class CreatedSearch:
    search: SelfieSearch
    public_token: str


def submit_selfie_search(*, event: Event, selfie: PreparedSelfie, storage) -> CreatedSearch:
    """Persist one validated selfie submission and its immutable current event cohort."""
    try:
        event = Event.objects.get(pk=event.pk, publication_status=Event.PublicationStatus.PUBLISHED)
    except Event.DoesNotExist:
        raise ValueError("selfie search requires a published event") from None

    content = selfie.content
    content_type = selfie.content_type
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
            SelfieSearchJob.objects.create(search=search, configuration=configuration)
    except Exception:
        try:
            storage.delete(key=stored.key)
        except Exception:
            pass
        raise
    return CreatedSearch(search=search, public_token=public_token)


def compatible_search_candidates(search: SelfieSearch) -> list[CandidateEmbedding]:
    """Load the compatible event cohort without persisting intermediate rows."""
    candidates = _compatible_candidates(event=search.event, configuration=search.configuration)
    search.eligible_photo_count = len({candidate.photo_id for candidate in candidates})
    search.eligible_face_count = len(candidates)
    search.save(update_fields=["eligible_photo_count", "eligible_face_count"])
    return candidates


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

    dimensions = configuration["embedding_dimensions"]
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ValueError("invalid embedding dimensions")
    return [
        CandidateEmbedding(
            vector=row.vector,
            model_version=row.model_version,
            detection_id=row.detection_id,
            photo_id=row.photo_id,
            photo_event_id=row.photo_event_id,
            attempt_event_id=row.attempt_event_id,
            attempt_photo_id=row.attempt_photo_id,
        )
        for row in load_compatible_face_embeddings(event, generations, dimensions)
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
