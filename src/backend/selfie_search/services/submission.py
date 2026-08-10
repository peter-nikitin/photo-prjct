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
from django.db.models import BooleanField, QuerySet
from django.db.models.expressions import RawSQL
from django.utils import timezone
from picflow.gallery import GalleryFaceCrop, gallery_face_crop, gallery_photo_queryset
from picflow.models import Event, Photo
from processing.models import (
    EventFaceClusterActivation,
    FaceEmbedding,
)
from processing.services.face_cohort import compatible_face_embedding_queryset
from processing.services.face_quality import active_face_embedding_generations

from selfie_search.images import PreparedSelfie
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
    SelfieSearchJob,
    SelfieSearchResult,
)
from selfie_search.services.cluster_expansion import (
    RankedPhotoExpansion,
    direct_only_ranked_photos,
    expand_ranked_photos,
)
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
    configuration = _configuration(
        event=event, content_type=content_type, content_size=len(content)
    )
    key = f"selfie-search/{uuid4().hex}"
    stored = storage.put(key=key, content=content, content_type=content_type)
    public_token = secrets.token_urlsafe(32)
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
    configuration = _gallery_configuration(event=event)
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
    """Validate one selected gallery face and create its queued bearer result."""
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

            configuration = _gallery_configuration(
                event=event, photo=photo, detection_id=detection_id
            )
            _gallery_source_candidate(event=event, configuration=configuration)

            public_token = secrets.token_urlsafe(32)
            search = SelfieSearch.objects.create(
                event=event,
                public_token_digest=_token_digest(public_token),
                temporary_object_key="",
                configuration=configuration,
                configuration_hash=_configuration_hash(configuration),
                state_changed_at=now,
            )
    except GallerySearchUnavailable:
        raise
    except DatabaseError as error:
        raise GallerySearchFailed() from error
    return CreatedSearch(search=search, public_token=public_token)


def process_gallery_photo_search(
    *, search: SelfieSearch, now: datetime | None = None
) -> SelfieSearch:
    """Publish a queued gallery-origin search once, under its row lock."""
    now = now or timezone.now()
    try:
        with transaction.atomic():
            locked_search = (
                SelfieSearch.objects.select_for_update().select_related("event").get(pk=search.pk)
            )
            if locked_search.status != SelfieSearch.Status.QUEUED:
                return locked_search
            if locked_search.configuration.get("processor") != "gallery_photo_query":
                raise GallerySearchUnavailable()

            source_candidate = _gallery_source_candidate(
                event=locked_search.event,
                configuration=locked_search.configuration,
            )
            candidates = _compatible_candidates(
                event=locked_search.event,
                configuration=locked_search.configuration,
            )
            ranked = rank_embeddings(locked_search, source_candidate.vector, candidates)
            source = locked_search.configuration.get("query_source")
            if not isinstance(source, dict) or not any(
                row.photo_id == source.get("photo_id") for row in ranked
            ):
                raise _MissingGallerySourceResult()
            expansion = _expand_gallery_ranking(
                search=locked_search,
                ranked=ranked,
                query=source_candidate.vector,
            )
            _persist_gallery_results(search=locked_search, expansion=expansion)
            locked_search.status = SelfieSearch.Status.READY
            locked_search.eligible_photo_count = len(
                {candidate.photo_id for candidate in candidates}
            )
            locked_search.eligible_face_count = len(candidates)
            locked_search.matched_photo_count = expansion.final_matched_photo_count
            locked_search.final_matched_photo_count = expansion.final_matched_photo_count
            locked_search.direct_matched_photo_count = expansion.direct_matched_photo_count
            locked_search.cluster_expanded_photo_count = expansion.cluster_expanded_photo_count
            locked_search.strong_anchor_count = expansion.strong_anchor_count
            locked_search.expanded_cluster_count = expansion.expanded_cluster_count
            locked_search.cluster_corpus_id = expansion.cluster_corpus_id
            locked_search.cluster_corpus_version = expansion.cluster_corpus_version
            locked_search.cluster_configuration_hash = expansion.cluster_configuration_hash
            locked_search.cluster_expansion_outcome = expansion.outcome
            locked_search.state_changed_at = now
            locked_search.terminal_at = now
            locked_search.cleanup_confirmed_at = now
            locked_search.save(
                update_fields=[
                    "status",
                    "eligible_photo_count",
                    "eligible_face_count",
                    "matched_photo_count",
                    "final_matched_photo_count",
                    "direct_matched_photo_count",
                    "cluster_expanded_photo_count",
                    "strong_anchor_count",
                    "expanded_cluster_count",
                    "cluster_corpus",
                    "cluster_corpus_version",
                    "cluster_configuration_hash",
                    "cluster_expansion_outcome",
                    "state_changed_at",
                    "terminal_at",
                    "cleanup_confirmed_at",
                ]
            )
    except GallerySearchUnavailable:
        return _terminal_gallery_failure(search_id=search.pk, status="search_unavailable", now=now)
    except (RankingError, _MissingGallerySourceResult):
        return _terminal_gallery_failure(search_id=search.pk, status="failed", now=now)
    except DatabaseError as error:
        raise GallerySearchFailed() from error
    return locked_search


def _terminal_gallery_failure(*, search_id, status: str, now: datetime) -> SelfieSearch:
    with transaction.atomic():
        search = SelfieSearch.objects.select_for_update().get(pk=search_id)
        if search.status != SelfieSearch.Status.QUEUED:
            return search
        search.status = status
        search.failure_code = status
        search.state_changed_at = now
        search.terminal_at = now
        search.cleanup_confirmed_at = now
        search.save(
            update_fields=[
                "status",
                "failure_code",
                "state_changed_at",
                "terminal_at",
                "cleanup_confirmed_at",
            ]
        )
    return search


def _gallery_source_candidate(
    *, event: Event, configuration: dict[str, object]
) -> CandidateEmbedding:
    if not Event.objects.filter(
        pk=event.pk, publication_status=Event.PublicationStatus.PUBLISHED
    ).exists():
        raise GallerySearchUnavailable()
    source = configuration.get("query_source")
    if (
        not isinstance(source, dict)
        or source.get("kind") != "gallery_photo"
        or not isinstance(source.get("photo_id"), str)
        or not isinstance(source.get("detection_id"), str)
    ):
        raise GallerySearchUnavailable()
    photo = gallery_photo_queryset(event=event).filter(pk=source["photo_id"]).first()
    if photo is None:
        raise GallerySearchUnavailable()
    row = (
        _compatible_gallery_embeddings(
            event=event,
            configuration=configuration,
            photo_ids=(str(photo.pk),),
        )
        .filter(detection_id=source["detection_id"])
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
    if row is None:
        raise GallerySearchUnavailable()
    (
        vector,
        model_version,
        detection_id,
        photo_id,
        photo_event_id,
        attempt_event_id,
        face_index,
        geometry,
    ) = row
    if (
        gallery_face_crop(detection_id=str(detection_id), face_index=face_index, geometry=geometry)
        is None
    ):
        raise GallerySearchUnavailable()
    candidate = CandidateEmbedding(
        vector=vector,
        model_version=model_version,
        detection_id=detection_id,
        photo_id=str(photo_id),
        photo_event_id=photo_event_id,
        attempt_event_id=attempt_event_id,
        attempt_photo_id=photo_id,
    )
    validation_search = SelfieSearch(event=event, configuration=configuration)
    if not _is_usable_gallery_query(search=validation_search, vector=candidate.vector):
        raise GallerySearchUnavailable()
    return candidate


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


def _configuration(*, event: Event, content_type: str, content_size: int) -> dict[str, object]:
    gallery_generations = _face_embedding_generations(event)
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


def _gallery_configuration(
    *, event: Event, photo: Photo | None = None, detection_id=None
) -> dict[str, object]:
    gallery_generations = _face_embedding_generations(event)
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

    embeddings = compatible_face_embedding_queryset(event, generations)
    if photo_ids is not None:
        embeddings = embeddings.filter(detection__attempt__photo_id__in=photo_ids)
    return embeddings


def _is_usable_gallery_query(*, search: SelfieSearch, vector: object) -> bool:
    try:
        validate_query_vector(search, vector)
    except RankingError:
        return False
    return True


def _expand_gallery_ranking(
    *, search: SelfieSearch, ranked: tuple, query: object
) -> RankedPhotoExpansion:
    if settings.SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED is not True:
        return direct_only_ranked_photos(ranked, outcome="disabled")
    activation = (
        EventFaceClusterActivation.objects.select_related("corpus")
        .filter(event=search.event, active=True)
        .first()
    )
    return expand_ranked_photos(search, ranked, query, activation)


def _persist_gallery_results(*, search: SelfieSearch, expansion: RankedPhotoExpansion) -> None:
    results = [
        SelfieSearchResult(
            search=search,
            photo_id=row.photo_id,
            primary_source=row.primary_source,
            rank=position,
        )
        for position, row in enumerate(expansion.results, start=1)
    ]
    SelfieSearchResult.objects.bulk_create(results)
    result_by_photo = {row.photo_id: row for row in results}
    SelfieSearchDirectEvidence.objects.bulk_create(
        [
            SelfieSearchDirectEvidence(
                result=result_by_photo[row.photo_id],
                detection_id=row.direct.detection_id,
                cosine_distance=row.direct.cosine_distance,
            )
            for row in expansion.results
            if row.direct is not None
        ]
    )
    SelfieSearchClusterEvidence.objects.bulk_create(
        [
            SelfieSearchClusterEvidence(
                result=result_by_photo[row.photo_id],
                corpus_id=expansion.cluster_corpus_id,
                cluster_id=evidence.cluster_id,
                anchor_result=result_by_photo[evidence.anchor_photo_id],
                anchor_detection_id=evidence.anchor_detection_id,
                member_detection_id=evidence.member_detection_id,
                representative_distance=evidence.representative_distance,
                source_order=evidence.source_order,
            )
            for row in expansion.results
            for evidence in row.cluster_evidence
        ]
    )


def _face_embedding_generations(event: Event) -> tuple[dict[str, object], ...]:
    return active_face_embedding_generations(event)


def _token_digest(public_token: str) -> str:
    return hashlib.sha256(public_token.encode("ascii")).hexdigest()


def _configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
