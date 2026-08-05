from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import BooleanField, Q, QuerySet
from django.db.models.expressions import RawSQL
from django.utils import timezone
from picflow.gallery import GalleryFaceCrop, gallery_face_crop, gallery_photo_queryset
from picflow.models import Event, Photo
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

from selfie_search.images import PreparedSelfie
from selfie_search.models import SelfieSearch, SelfieSearchJob, SelfieSearchResult
from selfie_search.services.ranking import (
    CandidateEmbedding,
    RankingError,
    rank_embeddings,
    validate_query_vector,
)


@dataclass(frozen=True)
class CreatedSearch:
    search: SelfieSearch
    public_token: str


class GallerySearchUnavailable(LookupError):
    """A selected gallery face is no longer usable current evidence."""


class GallerySearchFailed(RuntimeError):
    """A direct gallery search could not be atomically published."""


class _MissingGallerySourceResult(RuntimeError):
    pass


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


def gallery_search_faces_by_photo(
    *, event: Event, photos: Iterable[Photo]
) -> dict[str, tuple[GalleryFaceCrop, ...]]:
    """Return vector-free usable face crops for supplied gallery photos."""
    photo_ids = frozenset(str(photo.pk) for photo in photos if photo.event_id == event.pk)
    if not photo_ids:
        return {}
    configuration = _gallery_configuration()
    faces = _compatible_gallery_embeddings(
        event=event,
        configuration=configuration,
        photo_ids=photo_ids,
    ).order_by("detection__attempt__photo_id", "detection__face_index", "detection_id")
    results: dict[str, list[GalleryFaceCrop]] = {}
    for detection_id, photo_id, face_index, geometry in faces.values_list(
        "detection_id",
        "detection__attempt__photo_id",
        "detection__face_index",
        "detection__geometry",
    ).iterator(chunk_size=2_000):
        crop = gallery_face_crop(
            detection_id=str(detection_id), face_index=face_index, geometry=geometry
        )
        if crop is not None:
            results.setdefault(str(photo_id), []).append(crop)
    return {photo_id: tuple(crops) for photo_id, crops in results.items()}


def submit_gallery_photo_search(
    *, event: Event, photo: Photo, detection_id, now: datetime | None = None
) -> CreatedSearch:
    """Create an immediately-ready exact search from one selected gallery face."""
    now = now or timezone.now()
    try:
        with transaction.atomic():
            try:
                event = Event.objects.get(
                    pk=event.pk,
                    publication_status=Event.PublicationStatus.PUBLISHED,
                )
            except Event.DoesNotExist:
                raise GallerySearchUnavailable() from None
            photo = gallery_photo_queryset(event=event).filter(pk=photo.pk).first()
            if photo is None:
                raise GallerySearchUnavailable() from None

            configuration = _gallery_configuration(photo=photo, detection_id=detection_id)
            source = (
                _compatible_gallery_embeddings(
                    event=event,
                    configuration=configuration,
                    photo_ids=(str(photo.pk),),
                )
                .filter(detection_id=detection_id)
                .values_list(
                    "vector",
                    "model_version",
                    "detection_id",
                    "detection__attempt__photo_id",
                    "detection__attempt__photo__event_id",
                    "detection__attempt__event_id",
                    "detection__face_index",
                    "detection__geometry",
                )
                .first()
            )
            if source is None:
                raise GallerySearchUnavailable()
            (
                vector,
                model_version,
                source_detection_id,
                source_photo_id,
                photo_event_id,
                attempt_event_id,
                face_index,
                geometry,
            ) = source
            if (
                gallery_face_crop(
                    detection_id=str(source_detection_id), face_index=face_index, geometry=geometry
                )
                is None
            ):
                raise GallerySearchUnavailable()
            source_candidate = CandidateEmbedding(
                vector=vector,
                model_version=model_version,
                detection_id=source_detection_id,
                photo_id=str(source_photo_id),
                photo_event_id=photo_event_id,
                attempt_event_id=attempt_event_id,
                attempt_photo_id=source_photo_id,
            )
            validation_search = SelfieSearch(event=event, configuration=configuration)
            if not _is_usable_gallery_query(
                search=validation_search, vector=source_candidate.vector
            ):
                raise GallerySearchUnavailable()

            public_token = secrets.token_urlsafe(32)
            search = SelfieSearch.objects.create(
                event=event,
                public_token_digest=_token_digest(public_token),
                status=SelfieSearch.Status.READY,
                temporary_object_key="",
                configuration=configuration,
                configuration_hash=_configuration_hash(configuration),
                state_changed_at=now,
                terminal_at=now,
                cleanup_confirmed_at=now,
            )
            candidates = _compatible_candidates(event=event, configuration=configuration)
            ranked = rank_embeddings(search, source_candidate.vector, candidates)
            if not any(row.photo_id == str(photo.pk) for row in ranked):
                raise _MissingGallerySourceResult()
            SelfieSearchResult.objects.bulk_create(
                [
                    SelfieSearchResult(
                        search=search,
                        photo_id=row.photo_id,
                        detection_id=row.detection_id,
                        rank=position,
                        cosine_distance=row.cosine_distance,
                    )
                    for position, row in enumerate(ranked, start=1)
                ]
            )
            search.eligible_photo_count = len({candidate.photo_id for candidate in candidates})
            search.eligible_face_count = len(candidates)
            search.matched_photo_count = len(ranked)
            search.save(
                update_fields=[
                    "eligible_photo_count",
                    "eligible_face_count",
                    "matched_photo_count",
                ]
            )
    except GallerySearchUnavailable:
        raise
    except (DatabaseError, RankingError, _MissingGallerySourceResult) as error:
        raise GallerySearchFailed() from error
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


def _gallery_configuration(*, photo: Photo | None = None, detection_id=None) -> dict[str, object]:
    gallery_generations = _face_embedding_generations()
    gallery_model = gallery_generations[0]["model"]
    if not isinstance(gallery_model, str):
        raise ValueError("invalid face-embedding generation")
    configuration: dict[str, object] = {
        "contract_version": 1,
        "processor": "gallery_photo_query",
        "embedding_model": gallery_model,
        "embedding_dimensions": settings.SELFIE_SEARCH_EMBEDDING_DIMENSIONS,
        "cosine_distance_threshold": settings.SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD,
        "gallery_face_embedding_generations": list(gallery_generations),
    }
    if photo is not None:
        configuration["query_source"] = {
            "kind": "gallery_photo",
            "photo_id": str(photo.pk),
            "detection_id": str(detection_id),
        }
    return configuration


def _compatible_candidates(
    *,
    event: Event,
    configuration: dict[str, object],
    photo_ids: Iterable[str] | None = None,
):
    embeddings = _compatible_embeddings(
        event=event,
        configuration=configuration,
        photo_ids=photo_ids,
    )
    dimensions = configuration["embedding_dimensions"]
    return [
        CandidateEmbedding(
            vector=vector,
            model_version=model_version,
            detection_id=detection_id,
            photo_id=str(photo_id),
            photo_event_id=photo_event_id,
            attempt_event_id=attempt_event_id,
            attempt_photo_id=photo_id,
        )
        for vector, model_version, detection_id, photo_id, photo_event_id, attempt_event_id in (
            embeddings.order_by("detection_id")
            .values_list(
                "vector",
                "model_version",
                "detection_id",
                "detection__attempt__photo_id",
                "detection__attempt__photo__event_id",
                "detection__attempt__event_id",
            )
            .iterator(chunk_size=2_000)
        )
        if isinstance(vector, list) and len(vector) == dimensions
    ]


def _compatible_gallery_embeddings(
    *,
    event: Event,
    configuration: dict[str, object],
    photo_ids: Iterable[str] | None = None,
) -> QuerySet[FaceEmbedding]:
    return _compatible_embeddings(
        event=event,
        configuration=configuration,
        photo_ids=photo_ids,
    ).filter(
        _usable_vector_predicate(configuration),
        detection__geometry__coordinate_space="preview-small-v1",
        detection__geometry__pixel_width__gt=0,
        detection__geometry__pixel_height__gt=0,
        detection__geometry__bbox__isnull=False,
    )


def _usable_vector_predicate(configuration: dict[str, object]) -> RawSQL:
    dimensions = configuration.get("embedding_dimensions")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
        raise ValueError("invalid face-embedding dimensions")
    vector = f'"{FaceEmbedding._meta.db_table}"."vector"'
    return RawSQL(
        f"""
        jsonb_typeof({vector}) = 'array'
        AND jsonb_array_length({vector}) = %s
        AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements({vector}) AS face_vector(value)
            WHERE CASE
                WHEN jsonb_typeof(face_vector.value) = 'number'
                THEN NOT (
                    (face_vector.value #>> '{{}}')::double precision
                    > '-Infinity'::double precision
                    AND (face_vector.value #>> '{{}}')::double precision
                    < 'Infinity'::double precision
                )
                ELSE TRUE
            END
        )
        AND abs(
            sqrt(
                (
                    SELECT sum(
                        power(
                            CASE
                                WHEN jsonb_typeof(face_vector.value) = 'number'
                                THEN (face_vector.value #>> '{{}}')::double precision
                                ELSE 0
                            END,
                            2
                        )
                    )
                    FROM jsonb_array_elements({vector}) AS face_vector(value)
                )
            ) - 1
        ) <= %s
        """,
        (dimensions, 1e-6),
        output_field=BooleanField(),
    )


def _compatible_embeddings(
    *,
    event: Event,
    configuration: dict[str, object],
    photo_ids: Iterable[str] | None = None,
) -> QuerySet[FaceEmbedding]:
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
    embeddings = FaceEmbedding.objects.filter(
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
    ).filter(compatible_generation)
    if photo_ids is not None:
        embeddings = embeddings.filter(detection__attempt__photo_id__in=photo_ids)
    return embeddings


def _is_usable_gallery_query(*, search: SelfieSearch, vector: object) -> bool:
    try:
        validate_query_vector(search, vector)
    except RankingError:
        return False
    return True


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
