"""Select diverse direct anchors and compare their kept vectors inside PostgreSQL."""

from dataclasses import replace

from django.db import connection

from selfie_search.models import SelfieSearch, SelfieSearchResult
from selfie_search.services.cluster_expansion import ExpandedRankedPhoto, RankedPhotoExpansion
from selfie_search.services.ranking import AnchorMatch, RankedPhoto
from selfie_search.services.vector_ranking import accepted_kept_faces

_ANCHOR_DISTANCE = 0.38
_MATCH_DISTANCE = 0.42

# The greedy choices only compare a candidate to the previously selected anchors.
# WITH ORDINALITY preserves direct rank as the tie-breaker at each step.
_EXPAND_SQL = """
WITH kept_faces(photo_id, attempt_id, detection_id, embedding) AS NOT MATERIALIZED (
    {cohort_sql}
),
direct_matches AS (
    SELECT *
    FROM unnest(%s::varchar[], %s::uuid[], %s::float8[])
    WITH ORDINALITY AS direct(photo_id, detection_id, distance, rank)
),
anchor_candidates AS (
    SELECT direct.photo_id, direct.rank, face.embedding
    FROM direct_matches AS direct
    JOIN kept_faces AS face USING (photo_id, detection_id)
    WHERE direct.distance <= %s
      AND direct.photo_id IS DISTINCT FROM %s
      AND NOT EXISTS (
          SELECT 1
          FROM processing_photofacedetection AS other_face
          WHERE other_face.attempt_id = face.attempt_id
            AND other_face.id <> face.detection_id
      )
),
first_anchor AS (
    SELECT * FROM anchor_candidates ORDER BY rank LIMIT 1
),
second_anchor AS (
    SELECT candidate.*
    FROM anchor_candidates AS candidate
    CROSS JOIN first_anchor AS first
    WHERE candidate.rank <> first.rank
    ORDER BY (candidate.embedding <=> first.embedding) DESC, candidate.rank
    LIMIT 1
),
third_anchor AS (
    SELECT candidate.*
    FROM anchor_candidates AS candidate
    CROSS JOIN first_anchor AS first
    CROSS JOIN second_anchor AS second
    WHERE candidate.rank NOT IN (first.rank, second.rank)
    ORDER BY least(
        candidate.embedding <=> first.embedding,
        candidate.embedding <=> second.embedding
    ) DESC, candidate.rank
    LIMIT 1
),
anchors AS (
    SELECT photo_id, rank, embedding FROM first_anchor
    UNION ALL SELECT photo_id, rank, embedding FROM second_anchor
    UNION ALL SELECT photo_id, rank, embedding FROM third_anchor
),
best_matches AS (
    SELECT DISTINCT ON (face.photo_id)
        face.photo_id, anchor.photo_id AS anchor_photo_id, face.detection_id,
        greatest(0.0, face.embedding <=> anchor.embedding) AS distance
    FROM anchors AS anchor
    CROSS JOIN kept_faces AS face
    WHERE (face.embedding <=> anchor.embedding) <= %s
      AND NOT EXISTS (SELECT 1 FROM direct_matches WHERE photo_id = face.photo_id)
    ORDER BY face.photo_id, distance, anchor.rank, face.detection_id
)
SELECT photo_id, anchor_photo_id, detection_id, distance
FROM best_matches
ORDER BY distance, photo_id
"""


def find_anchor_matches(
    search: SelfieSearch,
    direct: tuple[RankedPhoto, ...],
    *,
    embedding_model: str,
    source_photo_id: str | None,
) -> tuple[AnchorMatch, ...]:
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
    with connection.cursor() as cursor:
        cursor.execute(
            _EXPAND_SQL.format(cohort_sql=cohort_sql),
            (
                *cohort_params,
                [row.photo_id for row in direct],
                [row.detection_id for row in direct],
                [row.cosine_distance for row in direct],
                _ANCHOR_DISTANCE,
                source_photo_id,
                _MATCH_DISTANCE,
            ),
        )
        return tuple(AnchorMatch(*row) for row in cursor.fetchall())


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
