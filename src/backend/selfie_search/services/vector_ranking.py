"""Exact current-vector ranking for retrievable event photos."""

from __future__ import annotations

from time import perf_counter
from uuid import UUID

from django.contrib.postgres.expressions import ArraySubquery
from django.core.cache import cache
from django.db.models import Count, Exists, F, Subquery, Value, Window
from django.db.models.functions import Greatest, JSONObject, Least, RowNumber
from pgvector.django import CosineDistance
from picflow.gallery import retrieval_gallery_photo_queryset
from processing.models import PhotoFaceDetection, PhotoProcessingState

from selfie_search.models import SelfieSearch
from selfie_search.services.direct_ranking import DirectRankingOutcome
from selfie_search.services.ranking import RankedPhoto, _configuration, validate_query_vector

_COUNT_CACHE_TTL_SECONDS = 60


def rank_vector_direct(search: SelfieSearch, query_vector: object) -> DirectRankingOutcome:
    """Rank current vectors; fresh matches and emptiness share one database snapshot."""
    configuration = _configuration(search)
    query = validate_query_vector(search, query_vector)
    photos = retrieval_gallery_photo_queryset(event=search.event).order_by().values("pk")
    states = (
        PhotoProcessingState.objects.filter(
            photo_id__in=Subquery(photos),
            processor_type="face_embedding",
            accepted_attempt_id__isnull=False,
            accepted_attempt__face_detections__status=PhotoFaceDetection.Status.KEPT,
            accepted_attempt__face_detections__embedding_vector__model_version=(
                configuration.model
            ),
        )
        .annotate(
            detection_id=F("accepted_attempt__face_detections__id"),
            distance=Least(
                Value(2.0),
                Greatest(
                    Value(0.0),
                    CosineDistance(
                        "accepted_attempt__face_detections__embedding_vector__vector",
                        list(query),
                    ),
                ),
            ),
        )
        .order_by()
    )
    matches = (
        states.annotate(
            photo_rank=Window(
                RowNumber(),
                partition_by=[F("photo_id")],
                order_by=[F("distance").asc(), F("detection_id").asc()],
            )
        )
        .filter(photo_rank=1, distance__lte=configuration.threshold)
        .order_by("distance", "photo_id", "detection_id")
        .annotate(
            payload=JSONObject(
                photo_id=F("photo_id"),
                detection_id=F("detection_id"),
                distance=F("distance"),
            )
        )
        .values("payload")
    )
    started = perf_counter()
    result = (
        SelfieSearch.objects.filter(pk=search.pk)
        .annotate(has_vectors=Exists(states), matches=ArraySubquery(matches))
        .values("has_vectors", "matches")
        .get()
    )
    if result["has_vectors"]:
        # Counts are advisory for one minute; only the fresh query controls results.
        cache_key = f"selfie-search:cohort-counts:v1:{search.event_id}:{configuration.model}"
        counts = cache.get(cache_key)
        if counts is None:
            sampled = states.aggregate(
                face_count=Count("pk"), photo_count=Count("photo_id", distinct=True)
            )
            counts = (sampled["face_count"], sampled["photo_count"])
            if counts[0]:
                cache.set(cache_key, counts, timeout=_COUNT_CACHE_TTL_SECONDS)
    else:
        counts = (0, 0)
    ranked_photos = tuple(
        RankedPhoto(
            photo_id=row["photo_id"],
            detection_id=UUID(row["detection_id"]),
            cosine_distance=row["distance"],
        )
        for row in result["matches"]
    )
    ranking_ms = (perf_counter() - started) * 1_000
    return DirectRankingOutcome(
        photos=ranked_photos,
        eligible_face_count=counts[0],
        eligible_photo_count=counts[1],
        has_vectors=result["has_vectors"],
        identity_ms=0.0,
        build_ms=0.0,
        ranking_ms=ranking_ms,
    )
