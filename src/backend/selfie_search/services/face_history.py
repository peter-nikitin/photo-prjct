"""Compare new result faces to the published history of one search."""

from django.db import connection
from psycopg import Cursor

from selfie_search.models import SelfieSearch
from selfie_search.services.cluster_expansion import RankedPhotoExpansion
from selfie_search.services.ranking import _configuration
from selfie_search.services.results import eligible_saved_result_rows

# Both readers supply evidence_rows(id, photo_id, search_id).
MATCHED_FACE_EVIDENCE_SQL = """
    SELECT result.id, result.photo_id, direct.detection_id,
        CASE WHEN direct.centroid_index IS NULL THEN direct.cosine_distance END AS direct_distance
    FROM evidence_rows result
    JOIN selfie_search_selfiesearchdirectevidence direct ON direct.result_id = result.id
    UNION ALL
    SELECT result.id, result.photo_id, anchor.detection_id, NULL
    FROM evidence_rows result
    JOIN selfie_search_selfiesearchanchorevidence anchor ON anchor.result_id = result.id
    UNION ALL
    SELECT result.id, result.photo_id, cluster.member_detection_id, NULL
    FROM evidence_rows result
    JOIN selfie_search_selfiesearchclusterevidence cluster ON cluster.result_id = result.id
    UNION ALL
    SELECT result.id, result.photo_id, (candidate->>'detection_id')::uuid, NULL
    FROM evidence_rows result
    JOIN selfie_search_selfiesearchapparelevidence apparel ON apparel.search_id = result.search_id
        AND apparel.decisions->'added_people' ? result.photo_id
    CROSS JOIN LATERAL jsonb_array_elements(apparel.decisions->'candidates') candidate
    WHERE candidate->>'photo_id' = result.photo_id
        AND candidate->>'person_id' = apparel.decisions->'added_people'->>result.photo_id
        AND candidate->>'detection_id' IS NOT NULL
"""

_FACE_HISTORY_SQL = """
WITH evidence_rows(id, photo_id, search_id) AS MATERIALIZED ({eligible_sql}),
history_faces AS ({matched_face_sql}),
proposed_faces(photo_id, detection_id) AS (
    SELECT * FROM unnest(%s::varchar[], %s::uuid[])
),
face_inputs AS (
    SELECT photo_id, detection_id, true AS historical FROM history_faces
    UNION ALL
    SELECT photo_id, detection_id, false FROM proposed_faces
),
verified_faces AS MATERIALIZED (
    SELECT input.photo_id, input.historical,
        (array_agg(DISTINCT input.detection_id))[1] AS detection_id
    FROM face_inputs input
    JOIN processing_photoprocessingstate state ON state.photo_id = input.photo_id
        AND state.processor_type = 'face_embedding'
    JOIN processing_photofacedetection detection ON detection.id = input.detection_id
        AND detection.attempt_id = state.accepted_attempt_id AND detection.status = 'kept'
    GROUP BY input.photo_id, input.historical
    HAVING count(DISTINCT input.detection_id) = 1
),
vectors AS MATERIALIZED (
    SELECT face.photo_id, face.historical, embedding.vector
    FROM verified_faces face
    JOIN processing_faceembeddingvector embedding ON embedding.detection_id = face.detection_id
        AND embedding.model_version = %s
    WHERE vector_norm(embedding.vector) > 0
),
history AS MATERIALIZED (SELECT vector FROM vectors WHERE historical)
SELECT candidate.photo_id
FROM vectors candidate
WHERE NOT candidate.historical
    AND EXISTS (SELECT 1 FROM history)
    AND NOT EXISTS (SELECT 1 FROM history WHERE (candidate.vector <=> history.vector) <= %s)
"""


def face_history_exclusions(
    *,
    parent: SelfieSearch,
    search: SelfieSearch,
    expansion: RankedPhotoExpansion,
    paid_previews_enabled: bool,
) -> frozenset[str]:
    """Veto candidates with a verified face when no earlier verified face supports the match."""
    inputs = [
        (row.photo_id, detection)
        for row in expansion.results
        if row.photo_id not in expansion.excluded_photo_ids
        for detection in {
            *(evidence.member_detection_id for evidence in row.cluster_evidence),
            *(
                match.detection_id
                for match in (row.direct, row.anchor_evidence)
                if match is not None
            ),
            row.apparel_detection_id,
        }
        if detection is not None
    ]
    if not inputs:
        return frozenset()
    # Explicit feedback is evaluated separately at publication so withdrawing a mark can
    # restore a match. The permanent history check depends only on saved match evidence.
    eligible_sql, eligible_params = (
        eligible_saved_result_rows(parent, paid_watermarked_previews_enabled=paid_previews_enabled)
        .order_by()
        .values_list("id", "photo_id", "search_id")
        .query.sql_with_params()
    )
    configuration = _configuration(search)
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute(
            _FACE_HISTORY_SQL.format(
                eligible_sql=eligible_sql, matched_face_sql=MATCHED_FACE_EVIDENCE_SQL
            ),
            [
                *eligible_params,
                [photo_id for photo_id, _ in inputs],
                [detection for _, detection in inputs],
                configuration.model,
                configuration.threshold,
            ],
        )
        return frozenset(photo_id for (photo_id,) in cursor.fetchall())
