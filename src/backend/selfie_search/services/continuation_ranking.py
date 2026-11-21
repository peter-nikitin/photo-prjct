"""Rank a capped batch of saved face centroids in one current-cohort SQL statement."""

from time import perf_counter

from django.db import connection
from django.db.models import QuerySet
from psycopg import Cursor

from selfie_search.models import SelfieSearch
from selfie_search.services.continuation import Anchor, _manual_source_photo_id
from selfie_search.services.direct_ranking import DirectRankingOutcome
from selfie_search.services.ranking import RankedPhoto, RankingError, _configuration
from selfie_search.services.results import eligible_saved_result_rows, family_excluded_photo_ids
from selfie_search.services.submission import CONTINUATION_PAID_PREVIEWS_KEY
from selfie_search.services.vector_ranking import accepted_kept_faces, cohort_counts

_CENTROID_RANKING_SQL = """
WITH cohort(photo_id, attempt_id, detection_id, embedding) AS MATERIALIZED (
    {cohort_sql}
),
anchor_inputs AS (
    SELECT * FROM unnest(%s::varchar[], %s::uuid[], %s::smallint[])
        AS input(photo_id, detection_id, centroid_index)
),
centroids AS (
    SELECT input.centroid_index,
        l2_normalize(avg(l2_normalize(cohort.embedding))) AS embedding,
        count(*) AS members
    FROM anchor_inputs input
    JOIN cohort USING (photo_id, detection_id)
    WHERE vector_norm(cohort.embedding) > 0
      AND NOT EXISTS (
          SELECT 1 FROM processing_photofacedetection other
          WHERE other.attempt_id = cohort.attempt_id AND other.id <> input.detection_id
      )
    GROUP BY input.centroid_index
),
best_matches AS (
    SELECT DISTINCT ON (face.photo_id)
        face.photo_id, face.detection_id, centroid.centroid_index,
        greatest(0.0, distance.value) AS distance
    FROM cohort face
    CROSS JOIN centroids centroid
    CROSS JOIN LATERAL (SELECT face.embedding <=> centroid.embedding AS value) distance
    WHERE distance.value <= %s
      AND face.photo_id IS DISTINCT FROM %s
      AND face.photo_id NOT IN ({prior_matches_sql})
    ORDER BY face.photo_id, distance, centroid.centroid_index, face.detection_id
)
SELECT validated.members, best.photo_id, best.detection_id, best.centroid_index, best.distance
FROM (SELECT sum(members) AS members FROM centroids) validated
LEFT JOIN best_matches best ON true
ORDER BY best.distance, best.photo_id
"""


def previous_matches(search: SelfieSearch) -> QuerySet:
    parent = search.parent_search
    if parent is None:
        raise RankingError("continuation has no parent")
    return (
        eligible_saved_result_rows(
            parent,
            paid_watermarked_previews_enabled=(
                search.configuration.get(CONTINUATION_PAID_PREVIEWS_KEY) is True
            ),
        )
        .order_by()
        .values("photo_id")
        .union(family_excluded_photo_ids(parent))
    )


def original_gallery_seed(search: SelfieSearch) -> str | None:
    parent = search.parent_search
    return _manual_source_photo_id(parent) if parent is not None else None


def rank_continuation_centroids(
    search: SelfieSearch, groups: tuple[tuple[Anchor, ...], ...]
) -> DirectRankingOutcome:
    configuration = _configuration(search)
    cohort_sql, cohort_params = (
        accepted_kept_faces(search, configuration.model)
        .values_list(
            "photo_id",
            "accepted_attempt_id",
            "accepted_attempt__face_detections__id",
            "accepted_attempt__face_detections__embedding_vector__vector",
        )
        .query.sql_with_params()
    )
    prior_sql, prior_params = previous_matches(search).query.sql_with_params()
    members = [
        (anchor.photo_id, anchor.detection_id, position)
        for position, group in enumerate(groups, start=1)
        for anchor in group
    ]
    started = perf_counter()
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute(
            _CENTROID_RANKING_SQL.format(cohort_sql=cohort_sql, prior_matches_sql=prior_sql),
            [
                *cohort_params,
                [member[0] for member in members],
                [member[1] for member in members],
                [member[2] for member in members],
                configuration.threshold,
                original_gallery_seed(search),
                *prior_params,
            ],
        )
        rows = cursor.fetchall()
    if rows[0][0] != len(members):
        raise RankingError("continuation centroid sources changed before matching")
    eligible_face_count, eligible_photo_count = cohort_counts(search, configuration.model)
    return DirectRankingOutcome(
        photos=tuple(
            RankedPhoto(
                photo_id=photo_id,
                detection_id=detection_id,
                cosine_distance=distance,
                centroid_index=position,
            )
            for _, photo_id, detection_id, position, distance in rows
            if photo_id is not None
        ),
        eligible_face_count=eligible_face_count,
        eligible_photo_count=eligible_photo_count,
        has_vectors=True,
        identity_ms=0.0,
        build_ms=0.0,
        ranking_ms=(perf_counter() - started) * 1_000,
    )
