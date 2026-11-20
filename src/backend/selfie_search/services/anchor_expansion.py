"""Rank face matches against one transient centroid inside PostgreSQL."""

from dataclasses import replace
from uuid import UUID

from django.db import connection
from psycopg import Cursor

from selfie_search.models import SelfieSearch, SelfieSearchResult
from selfie_search.services.cluster_expansion import ExpandedRankedPhoto, RankedPhotoExpansion
from selfie_search.services.ranking import AnchorMatch, RankedPhoto
from selfie_search.services.vector_ranking import accepted_kept_faces

_ANCHOR_DISTANCE = 0.38
_ANCHOR_CONSISTENCY_DISTANCE = 0.35
_MATCH_DISTANCE = 0.42

_EXPAND_SQL = """
WITH kept_faces(photo_id, attempt_id, detection_id, embedding) AS NOT MATERIALIZED (
    {cohort_sql}
),
direct_matches AS (
    SELECT *
    FROM unnest(%s::varchar[], %s::uuid[], %s::float8[])
    AS direct(photo_id, detection_id, distance)
),
anchor_candidates AS (
    SELECT direct.photo_id, direct.detection_id, direct.distance, face.embedding
    FROM direct_matches AS direct
    JOIN kept_faces AS face USING (photo_id, detection_id)
    WHERE direct.distance <= %s
      AND direct.photo_id IS DISTINCT FROM %s
      AND (%s::uuid[] IS NULL OR direct.detection_id = ANY(%s::uuid[]))
      AND NOT EXISTS (
          SELECT 1
          FROM processing_photofacedetection AS other_face
          WHERE other_face.attempt_id = face.attempt_id
            AND other_face.id <> face.detection_id
      )
),
first_anchor AS (
    SELECT embedding
    FROM anchor_candidates
    ORDER BY distance, photo_id, detection_id
    LIMIT 1
),
anchors AS (
    SELECT candidate.*
    FROM anchor_candidates AS candidate
    CROSS JOIN first_anchor AS first
    WHERE (candidate.embedding <=> first.embedding) <= %s
    ORDER BY candidate.distance, candidate.photo_id, candidate.detection_id
    LIMIT 5
),
centroid AS (
    SELECT
        l2_normalize(avg(l2_normalize(embedding))) AS embedding,
        array_agg(photo_id ORDER BY distance, photo_id, detection_id) AS anchor_photo_ids
    FROM anchors
    HAVING count(*) > 0
),
best_matches AS (
    SELECT DISTINCT ON (face.photo_id)
        face.photo_id, face.detection_id,
        greatest(0.0, face.embedding <=> centroid.embedding) AS distance
    FROM centroid
    CROSS JOIN kept_faces AS face
    WHERE (face.embedding <=> centroid.embedding) <= %s
      AND NOT EXISTS (SELECT 1 FROM direct_matches WHERE photo_id = face.photo_id)
    ORDER BY face.photo_id, distance, face.detection_id
)
SELECT photo_id, anchor_photo_ids, detection_id, distance
FROM best_matches
CROSS JOIN centroid
ORDER BY distance, photo_id
"""


def find_anchor_matches(
    search: SelfieSearch,
    direct: tuple[RankedPhoto, ...],
    *,
    embedding_model: str,
    source_photo_id: str | None,
    verified_detection_ids: tuple[UUID, ...] | None = None,
) -> tuple[AnchorMatch, ...]:
    if verified_detection_ids == ():
        return ()
    verified = list(verified_detection_ids) if verified_detection_ids is not None else None
    cohort_sql, cohort_params = (
        accepted_kept_faces(search, embedding_model)
        .values_list(
            "photo_id",
            "accepted_attempt_id",
            "accepted_attempt__face_detections__id",
            "accepted_attempt__face_detections__embedding_vector__vector",
        )
        .query.sql_with_params()
    )
    # Bind match provenance on the server so values stay out of logged error statements.
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute("SET LOCAL log_parameter_max_length_on_error = 0")
        cursor.execute(
            _EXPAND_SQL.format(cohort_sql=cohort_sql),
            (
                *cohort_params,
                [row.photo_id for row in direct],
                [row.detection_id for row in direct],
                [row.cosine_distance for row in direct],
                _ANCHOR_DISTANCE,
                source_photo_id,
                verified,
                verified,
                _ANCHOR_CONSISTENCY_DISTANCE,
                _MATCH_DISTANCE,
            ),
        )
        return tuple(
            AnchorMatch(photo_id, tuple(anchor_photo_ids), detection_id, distance)
            for photo_id, anchor_photo_ids, detection_id, distance in cursor.fetchall()
        )


def append_anchor_matches(
    expansion: RankedPhotoExpansion, matches: tuple[AnchorMatch, ...]
) -> RankedPhotoExpansion:
    seen = {row.photo_id for row in expansion.results}
    additions = tuple(
        ExpandedRankedPhoto(
            photo_id=match.photo_id,
            primary_source=str(SelfieSearchResult.PrimarySource.FACE_ANCHOR_EXPANSION),
            direct=None,
            cluster_evidence=(),
            anchor_evidence=match,
        )
        for match in matches
        if match.photo_id not in seen
    )
    return replace(
        expansion,
        results=expansion.results + additions,
        final_matched_photo_count=expansion.final_matched_photo_count + len(additions),
        anchor_expanded_photo_count=len(additions),
    )
