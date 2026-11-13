from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from picflow.gallery import (
    GalleryFaceCrop,
    gallery_face_crop,
    gallery_photo_queryset,
    retrieval_gallery_photo_queryset,
)
from picflow.models import Event, Photo
from processing.models import (
    FaceEmbeddingVector,
    PhotoFaceDetection,
    PhotoProcessingState,
)
from processing.services.face_quality import active_face_embedding_generations

from selfie_search.images import PreparedSelfie
from selfie_search.models import (
    FaceSearchTask,
    SelfieSearch,
    SelfieSearchJob,
)
from selfie_search.services.jobs import enqueue_face_search
from selfie_search.services.ranking import RankingError, validate_query_vector


@dataclass(frozen=True)
class CreatedSearch:
    search: SelfieSearch
    public_token: str


class GallerySearchUnavailable(LookupError):
    """A selected gallery face is no longer usable current evidence."""


class GallerySearchFailed(RuntimeError):
    """A direct gallery search could not be atomically published."""


def submit_selfie_search(*, event: Event, selfie: PreparedSelfie, storage, user) -> CreatedSearch:
    """Persist one validated selfie submission and its immutable current event cohort."""
    try:
        event = Event.objects.site_visible_to(user).get(pk=event.pk)
    except Event.DoesNotExist:
        raise ValueError("selfie search requires an event visible to the current user") from None

    content = selfie.content
    content_type = selfie.content_type
    key = f"selfie-search/{uuid4().hex}"
    stored = storage.put(key=key, content=content, content_type=content_type)
    public_token = secrets.token_urlsafe(32)
    try:
        with transaction.atomic():
            try:
                event = Event.objects.site_visible_to(user).select_for_update().get(pk=event.pk)
            except Event.DoesNotExist:
                raise ValueError(
                    "selfie search requires an event visible to the current user"
                ) from None
            configuration = _configuration(
                event=event, content_type=content_type, content_size=len(content)
            )
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
    *,
    event: Event,
    photos: Iterable[Photo],
    paid_watermarked_previews_enabled: bool = False,
) -> dict[str, tuple[GalleryFaceCrop, ...]]:
    """Return vector-free usable face crops for supplied gallery photos."""
    photo_ids = frozenset(str(photo.pk) for photo in photos if photo.event_id == event.pk)
    if not photo_ids:
        return {}
    configuration = _gallery_configuration(event=event)
    visible_ids = (
        gallery_photo_queryset(
            event=event,
            paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
        )
        .filter(pk__in=photo_ids)
        .values("pk")
    )
    attempt_to_photo = dict(
        PhotoProcessingState.objects.filter(
            photo_id__in=visible_ids,
            processor_type="face_embedding",
            accepted_attempt_id__isnull=False,
        ).values_list("accepted_attempt_id", "photo_id")
    )
    if not attempt_to_photo:
        return {}
    faces = _usable_gallery_vectors(
        accepted_attempt_ids=attempt_to_photo,
        model=configuration["embedding_model"],
    ).order_by("detection__attempt_id", "detection__face_index", "detection_id")
    results: dict[str, list[GalleryFaceCrop]] = {}
    for detection_id, attempt_id, face_index, geometry in faces.values_list(
        "detection_id",
        "detection__attempt_id",
        "detection__face_index",
        "detection__geometry",
    ).iterator(chunk_size=2_000):
        crop = gallery_face_crop(
            detection_id=str(detection_id), face_index=face_index, geometry=geometry
        )
        if crop is not None:
            results.setdefault(str(attempt_to_photo[attempt_id]), []).append(crop)
    return {photo_id: tuple(crops) for photo_id, crops in results.items()}


@sensitive_variables("query")
def submit_gallery_photo_search(
    *,
    event: Event,
    photo: Photo,
    detection_id,
    user,
    now: datetime | None = None,
    paid_watermarked_previews_enabled: bool = False,
) -> CreatedSearch:
    """Validate one selected gallery face and create its queued bearer result."""
    now = now or timezone.now()
    try:
        with transaction.atomic():
            try:
                event = Event.objects.site_visible_to(user).select_for_update().get(pk=event.pk)
            except Event.DoesNotExist:
                raise GallerySearchUnavailable() from None
            photo = (
                gallery_photo_queryset(
                    event=event,
                    paid_watermarked_previews_enabled=paid_watermarked_previews_enabled,
                )
                .filter(pk=photo.pk)
                .first()
            )
            if photo is None:
                raise GallerySearchUnavailable() from None

            configuration = _gallery_configuration(
                event=event, photo=photo, detection_id=detection_id
            )
            query = _gallery_source_vector(event=event, configuration=configuration)

            public_token = secrets.token_urlsafe(32)
            search = SelfieSearch.objects.create(
                event=event,
                public_token_digest=_token_digest(public_token),
                temporary_object_key="",
                configuration=configuration,
                configuration_hash=_configuration_hash(configuration),
                state_changed_at=now,
            )
            enqueue_face_search(
                search,
                source=str(FaceSearchTask.Source.GALLERY),
                query=query,
                now=search.created_at,
            )
    except GallerySearchUnavailable:
        raise
    except DatabaseError as error:
        raise GallerySearchFailed() from error
    return CreatedSearch(search=search, public_token=public_token)


def _gallery_source_vector(
    *,
    event: Event,
    configuration: dict[str, object],
) -> tuple[float, ...]:
    source = configuration.get("query_source")
    if (
        not isinstance(source, dict)
        or source.get("kind") != "gallery_photo"
        or not isinstance(source.get("photo_id"), str)
        or not isinstance(source.get("detection_id"), str)
    ):
        raise GallerySearchUnavailable()
    photo = retrieval_gallery_photo_queryset(event=event).filter(pk=source["photo_id"]).first()
    if photo is None:
        raise GallerySearchUnavailable()
    accepted_attempt_id = (
        PhotoProcessingState.objects.filter(
            photo_id=photo.pk,
            processor_type="face_embedding",
        )
        .values_list("accepted_attempt_id", flat=True)
        .first()
    )
    if accepted_attempt_id is None:
        raise GallerySearchUnavailable()
    row = (
        _usable_gallery_vectors(
            accepted_attempt_ids=(accepted_attempt_id,),
            model=configuration.get("embedding_model"),
        )
        .filter(detection_id=source["detection_id"])
        .values_list(
            "vector",
            "model_version",
            "detection_id",
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
        face_index,
        geometry,
    ) = row
    if model_version != configuration.get("embedding_model"):
        raise GallerySearchUnavailable()
    if (
        gallery_face_crop(detection_id=str(detection_id), face_index=face_index, geometry=geometry)
        is None
    ):
        raise GallerySearchUnavailable()
    validation_search = SelfieSearch(event=event, configuration=configuration)
    try:
        return validate_query_vector(validation_search, [float(value) for value in vector])
    except RankingError:
        raise GallerySearchUnavailable() from None


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
    embedding_dimensions, distance_threshold = _search_parameters(gallery_model)
    return {
        "contract_version": 1,
        "processor": "selfie_query",
        "embedding_model": gallery_model,
        "embedding_dimensions": embedding_dimensions,
        "cosine_distance_threshold": distance_threshold,
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
    embedding_dimensions, distance_threshold = _search_parameters(gallery_model)
    configuration: dict[str, object] = {
        "contract_version": 1,
        "processor": "gallery_photo_query",
        "embedding_model": gallery_model,
        "embedding_dimensions": embedding_dimensions,
        "cosine_distance_threshold": distance_threshold,
        "gallery_face_embedding_generations": list(gallery_generations),
    }
    if photo is not None:
        configuration["query_source"] = {
            "kind": "gallery_photo",
            "photo_id": str(photo.pk),
            "detection_id": str(detection_id),
        }
    return configuration


def _search_parameters(model: str) -> tuple[int, float]:
    if model == "adaface-ir18-webface4m":
        return 512, 0.42
    raise ValueError("invalid face-embedding generation")


def _usable_gallery_vectors(
    *, accepted_attempt_ids: Iterable, model: object
) -> QuerySet[FaceEmbeddingVector]:
    return FaceEmbeddingVector.objects.filter(
        detection__attempt_id__in=accepted_attempt_ids,
        detection__status=PhotoFaceDetection.Status.KEPT,
        model_version=model,
        detection__geometry__coordinate_space="preview-small-v1",
        detection__geometry__pixel_width__gt=0,
        detection__geometry__pixel_height__gt=0,
        detection__geometry__bbox__isnull=False,
    )


def _face_embedding_generations(event: Event) -> tuple[dict[str, object], ...]:
    return active_face_embedding_generations(event)


def _token_digest(public_token: str) -> str:
    return hashlib.sha256(public_token.encode("ascii")).hexdigest()


def _configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
