"""Read reversible manual and face-similarity feedback for one saved result family."""

from dataclasses import dataclass
from uuid import UUID

from django.db import connection
from django.db.models import QuerySet
from django.db.models.fields.json import KeyTextTransform
from picflow.gallery import retrieval_gallery_photo_queryset
from psycopg import Cursor
from search_feedback.models import SearchFeedback
from search_feedback.services import active_negative_reports, face_result_reports

from selfie_search.models import SelfieSearch
from selfie_search.services.face_history import MATCHED_FACE_EVIDENCE_SQL
from selfie_search.services.ranking import RankingError, _configuration
from selfie_search.services.results import eligible_saved_result_rows, ready_family_results

_NEGATIVE_DISTANCE = 0.25
_STRONG_POSITIVE_DISTANCE = 0.34
_POSITIVE_AMBIGUITY_MARGIN = 0.04
_MAX_STRONG_POSITIVES = 20


@dataclass(frozen=True, slots=True)
class NegativeFeedbackState:
    manual_photo_ids: frozenset[str] = frozenset()
    similar_photo_ids: frozenset[str] = frozenset()
    excluded_photo_ids: frozenset[str] = frozenset()


# The candidate is always the saved matched face. A face elsewhere in the same photo cannot
# supply negative evidence. Distances, vectors and propagation stay within one SQL read.
_SIMILAR_FACE_SQL = """
WITH visible_rows(id, photo_id, search_id, created_at, search_created_at) AS MATERIALIZED (
    {eligible_sql}
),
first_results AS MATERIALIZED (
    SELECT DISTINCT ON (photo_id) id, photo_id, search_id, created_at, search_created_at
    FROM visible_rows
    ORDER BY photo_id, search_created_at, search_id
),
manual(photo_id, result_id, marked_at) AS (
    SELECT * FROM unnest(%(manual_photos)s::varchar[], %(manual_results)s::text[],
        %(manual_times)s::timestamptz[])
),
report_rows(id, photo_id, search_id, created_at, search_created_at) AS MATERIALIZED (
    {report_sql}
),
marked_results AS (
    SELECT DISTINCT ON (report.photo_id) report.photo_id, result.id, report.marked_at
    FROM manual report
    LEFT JOIN first_results first USING (photo_id)
    JOIN report_rows result ON result.photo_id = report.photo_id
        AND result.id::text = coalesce(report.result_id, first.id::text, result.id::text)
    ORDER BY report.photo_id, report.marked_at, result.search_created_at, result.id
),
evidence_rows AS MATERIALIZED (
    SELECT id, photo_id, search_id FROM first_results
    UNION
    SELECT saved.id, saved.photo_id, saved.search_id
    FROM marked_results reference JOIN report_rows saved USING (id, photo_id)
),
matched_faces AS (
    {matched_face_sql}
),
unambiguous_faces AS MATERIALIZED (
    SELECT match.id, match.photo_id, (array_agg(DISTINCT match.detection_id))[1] AS detection_id,
        min(match.direct_distance) AS direct_distance
    FROM matched_faces match
    JOIN processing_photoprocessingstate state ON state.photo_id = match.photo_id
        AND state.processor_type = 'face_embedding'
    JOIN processing_photofacedetection face ON face.id = match.detection_id
        AND face.attempt_id = state.accepted_attempt_id AND face.status = 'kept'
    GROUP BY match.id, match.photo_id
    HAVING count(DISTINCT match.detection_id) = 1
),
vectors AS MATERIALIZED (
    SELECT face.id, face.photo_id, face.detection_id, face.direct_distance, embedding.vector
    FROM unambiguous_faces face
    JOIN processing_faceembeddingvector embedding ON embedding.detection_id = face.detection_id
        AND embedding.model_version = %(model)s
    WHERE vector_norm(embedding.vector) > 0
),
negative_vectors AS (
    SELECT reference.photo_id, reference.marked_at, vector.vector
    FROM marked_results reference JOIN vectors vector USING (id, photo_id)
),
positive_gallery AS (
    SELECT state.photo_id, detection.id AS detection_id, embedding.vector
    FROM processing_photoprocessingstate state
    JOIN processing_photofacedetection detection ON detection.attempt_id = state.accepted_attempt_id
        AND detection.id = %(gallery_detection)s::uuid AND detection.status = 'kept'
    JOIN processing_faceembeddingvector embedding ON embedding.detection_id = detection.id
        AND embedding.model_version = %(model)s
    WHERE state.photo_id = %(gallery_photo)s AND state.processor_type = 'face_embedding'
        AND state.photo_id IN ({gallery_sql})
        AND NOT (state.photo_id = ANY(%(manual_photos)s::varchar[]))
        AND vector_norm(embedding.vector) > 0
),
positive_vectors(photo_id, detection_id, vector, explicitly_selected) AS MATERIALIZED (
    SELECT photo_id, detection_id, vector, true FROM positive_gallery
    UNION ALL
    SELECT face.photo_id, face.detection_id, face.vector, true
    FROM vectors face JOIN first_results USING (id, photo_id)
    WHERE EXISTS (
        SELECT 1 FROM unnest(%(present_photos)s::varchar[], %(present_results)s::text[])
            AS report(photo_id, result_id)
        WHERE report.photo_id = face.photo_id
            AND report.result_id = face.id::text
    )
        AND NOT (face.photo_id = ANY(%(manual_photos)s::varchar[]))
    UNION ALL
    SELECT strong.photo_id, strong.detection_id, strong.vector, false FROM (
        SELECT face.photo_id, face.detection_id, face.vector
        FROM vectors face JOIN first_results USING (id, photo_id)
        WHERE face.direct_distance <= %(positive_distance)s
            AND NOT (face.photo_id = ANY(%(manual_photos)s::varchar[]))
            AND NOT EXISTS (
                SELECT 1 FROM negative_vectors negative
                WHERE (face.vector <=> negative.vector) <= %(negative_distance)s
            )
        ORDER BY face.direct_distance, face.photo_id
        LIMIT %(positive_limit)s
    ) strong
),
history_positive_vectors AS MATERIALIZED (
    SELECT face.photo_id, face.vector, result.search_created_at, result.search_id
    FROM vectors face JOIN first_results result USING (id, photo_id)
    WHERE NOT (face.photo_id = ANY(%(manual_photos)s::varchar[]))
        AND NOT EXISTS (
            SELECT 1 FROM negative_vectors negative
            WHERE (face.vector <=> negative.vector) <= %(negative_distance)s
        )
),
near_matches AS (
    SELECT candidate.photo_id, candidate.detection_id, candidate.vector,
        result.search_created_at, result.search_id,
        match.distance, match.prior_distance
    FROM first_results result
    JOIN vectors candidate USING (id, photo_id)
    CROSS JOIN LATERAL (
        SELECT min(distance.value) AS distance,
            min(distance.value) FILTER (WHERE negative.marked_at <= result.created_at
                AND result.search_id <> %(root_id)s) AS prior_distance
        FROM negative_vectors negative
        CROSS JOIN LATERAL (SELECT candidate.vector <=> negative.vector AS value) distance
        WHERE distance.value <= %(negative_distance)s
    ) match
    WHERE match.distance IS NOT NULL
        AND NOT (candidate.photo_id = ANY(%(manual_photos)s::varchar[]))
)
SELECT near.photo_id, verdict.exclude_new
FROM near_matches near
CROSS JOIN LATERAL (
    SELECT min(reference.vector <=> near.vector) AS distance,
        min(reference.vector <=> near.vector) FILTER (WHERE reference.explicitly_selected)
            AS explicit_distance
    FROM positive_vectors reference
    WHERE reference.photo_id <> near.photo_id
        OR (reference.explicitly_selected AND reference.detection_id = near.detection_id)
) positive
CROSS JOIN LATERAL (
    SELECT min(reference.vector <=> near.vector) AS distance
    FROM history_positive_vectors reference
    WHERE near.prior_distance IS NOT NULL
        AND (reference.search_created_at, reference.search_id) <
            (near.search_created_at, near.search_id)
) history
CROSS JOIN LATERAL (
    SELECT near.prior_distance IS NOT NULL
        AND (least(positive.explicit_distance, history.distance) IS NULL
            OR least(positive.explicit_distance, history.distance) >
                near.prior_distance + %(ambiguity_margin)s) AS exclude_new
) verdict
WHERE verdict.exclude_new
    OR positive.distance IS NULL OR positive.distance > near.distance + %(ambiguity_margin)s
"""


def negative_feedback_state(search: SelfieSearch) -> NegativeFeedbackState:
    """Read saved decisions without comparing face vectors."""
    manual = frozenset(active_negative_reports(search).values_list("photo_id", flat=True))
    return NegativeFeedbackState(
        manual_photo_ids=manual,
        similar_photo_ids=frozenset(
            photo_id
            for photo_id, state in search.negative_feedback_photos.items()
            if state == "similar"
        ),
        excluded_photo_ids=frozenset(
            photo_id
            for photo_id, state in search.negative_feedback_photos.items()
            if state == "excluded"
        ),
    )


def refresh_negative_feedback(search: SelfieSearch) -> NegativeFeedbackState:
    """Recompute the photo flags while the caller holds the root search lock."""
    eligible = eligible_saved_result_rows(search, paid_watermarked_previews_enabled=True).order_by()
    state = _compare_negative_faces(search, eligible)
    flags = {photo_id: "similar" for photo_id in state.similar_photo_ids}
    flags.update({photo_id: "excluded" for photo_id in state.excluded_photo_ids})
    if flags != search.negative_feedback_photos:
        search.negative_feedback_photos = flags
        search.save(update_fields=["negative_feedback_photos"])
    return state


def _compare_negative_faces(search: SelfieSearch, eligible: QuerySet) -> NegativeFeedbackState:
    reports = list(
        active_negative_reports(search)
        .annotate(saved_result_id=KeyTextTransform("result_id", "search_context"))
        .values_list("photo_id", "saved_result_id", "created_at")
    )
    if not reports:
        return NegativeFeedbackState()
    manual = frozenset(photo_id for photo_id, _, _ in reports)
    try:
        model = _configuration(search).model
    except RankingError:
        return NegativeFeedbackState(manual_photo_ids=manual)

    gallery_source = search.configuration.get("query_source")
    if not isinstance(gallery_source, dict) or gallery_source.get("kind") != "gallery_photo":
        gallery_source = {}
    try:
        gallery_detection = UUID(str(gallery_source.get("detection_id")))
    except ValueError:
        gallery_detection = None
    present_reports = list(
        face_result_reports(search)
        .filter(value=SearchFeedback.Value.PRESENT, withdrawn_at__isnull=True)
        .annotate(saved_result_id=KeyTextTransform("result_id", "search_context"))
        .values_list("photo_id", "saved_result_id")
        .distinct()
    )
    query_params = {
        "model": model,
        "root_id": search.pk,
        "manual_photos": [photo_id for photo_id, _, _ in reports],
        "manual_results": [result_id for _, result_id, _ in reports],
        "manual_times": [marked_at for _, _, marked_at in reports],
        "present_photos": [photo_id for photo_id, _ in present_reports],
        "present_results": [result_id for _, result_id in present_reports],
        "gallery_photo": gallery_source.get("photo_id"),
        "gallery_detection": gallery_detection,
        "negative_distance": _NEGATIVE_DISTANCE,
        "positive_distance": _STRONG_POSITIVE_DISTANCE,
        "ambiguity_margin": _POSITIVE_AMBIGUITY_MARGIN,
        "positive_limit": _MAX_STRONG_POSITIVES,
    }
    eligible_sql, eligible_params = eligible.values_list(
        "id", "photo_id", "search_id", "created_at", "search__created_at"
    ).query.sql_with_params()
    gallery_sql, gallery_params = (
        retrieval_gallery_photo_queryset(event=search.event)
        .order_by()
        .values("pk")
        .query.sql_with_params()
    )
    report_sql, report_params = (
        ready_family_results(search)
        .filter(photo_id__in=manual)
        .values_list("id", "photo_id", "search_id", "created_at", "search__created_at")
        .query.sql_with_params()
    )
    for name, sql, params in (
        ("report", report_sql, report_params),
        ("eligible", eligible_sql, eligible_params),
        ("gallery", gallery_sql, gallery_params),
    ):
        for index, value in enumerate(params):
            key = f"{name}_{index}"
            sql = sql.replace("%s", f"%({key})s", 1)
            query_params[key] = value
        query_params[f"{name}_sql"] = sql
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute(
            _SIMILAR_FACE_SQL.format(
                eligible_sql=query_params.pop("eligible_sql"),
                gallery_sql=query_params.pop("gallery_sql"),
                report_sql=query_params.pop("report_sql"),
                matched_face_sql=MATCHED_FACE_EVIDENCE_SQL,
            ),
            query_params,
        )
        matches = cursor.fetchall()
    return NegativeFeedbackState(
        manual_photo_ids=manual,
        similar_photo_ids=frozenset(photo_id for photo_id, exclude in matches if not exclude),
        excluded_photo_ids=frozenset(photo_id for photo_id, exclude in matches if exclude),
    )
