"""Bounded appearance retrieval using individual accepted person descriptors."""

from dataclasses import replace
from typing import Any
from uuid import UUID

from django.db import connection
from picflow.gallery import retrieval_gallery_photo_queryset
from processing.appearance import APPEARANCE_CONFIGURATION
from processing.services.enrollment import _configuration_hash
from psycopg import Cursor

from selfie_search.models import SelfieSearch, SelfieSearchResult
from selfie_search.services.cluster_expansion import ExpandedRankedPhoto, RankedPhotoExpansion
from selfie_search.services.ranking import RankedPhoto

POLICY: dict[str, int | float] = {
    "version": 1,
    "anchor_distance": 0.38,
    "anchor_consistency_distance": 0.35,
    "max_anchors": 5,
    "reid_cosine_similarity_min": 0.70,
    "helmet_cosine_similarity_min": 0.68,
    "face_cosine_distance_max": 0.55,
    "reid_rank_weight": 0.8,
    "max_candidates": 200,
    "max_additions": 100,
    "max_recommendations": 100,
}


def candidate_veto(*, face_distance: float | None, helmet_similarity: float | None) -> str:
    if face_distance is not None and face_distance > POLICY["face_cosine_distance_max"]:
        return "face_veto"
    if helmet_similarity is not None and helmet_similarity < POLICY["helmet_cosine_similarity_min"]:
        return "helmet_veto"
    return ""


_QUERY = """
WITH found_faces AS (
    SELECT * FROM unnest(%(found_photos)s::varchar[], %(found_detections)s::uuid[],
        %(found_ranks)s::int[]) AS found(photo_id, detection_id, result_rank)
),
direct AS (
    SELECT * FROM unnest(%(ids)s::varchar[], %(detections)s::uuid[], %(distances)s::float8[])
    AS direct(photo_id, detection_id, distance)
),
people AS MATERIALIZED (
    SELECT appearance.id AS person_id, state.photo_id, appearance.head, appearance.reason,
        appearance.reid, appearance.color, appearance.helmet,
        (attempt.result->>'head_count')::int AS head_count,
        face_state.accepted_attempt_id AS face_attempt, attempt.input_fingerprint AS fingerprint
    FROM processing_photoprocessingstate state
    JOIN processing_processingattempt attempt ON attempt.id = state.accepted_attempt_id
    JOIN processing_processingjob job ON job.id = attempt.job_id
    JOIN processing_personappearancedetection appearance ON appearance.attempt_id = attempt.id
    JOIN processing_photoprocessingstate preview ON preview.photo_id = state.photo_id
        AND preview.processor_type = 'generate_preview' AND preview.status = 'succeeded'
    JOIN processing_photoderivative derivative ON derivative.photo_id = state.photo_id
        AND derivative.variant = 'preview-small-v1'
        AND derivative.accepted_attempt_id = preview.accepted_attempt_id
    LEFT JOIN processing_photoprocessingstate face_state ON face_state.photo_id = state.photo_id
        AND face_state.processor_type = 'face_embedding' AND face_state.status = 'succeeded'
    WHERE state.processor_type = 'person_appearance'
        AND state.status = 'succeeded' AND attempt.accepted
        AND attempt.processor_version = 1 AND attempt.contract_version = 2
        AND attempt.event_id = %(event)s AND attempt.status = 'succeeded'
        AND job.configuration_hash = %(config)s
        AND state.photo_id IN ({cohort_sql})
        AND attempt.input_fingerprint = jsonb_build_object(
            'object_key', derivative.final_key, 'object_size', derivative.byte_size,
            'object_content_type', derivative.content_type, 'object_etag', NULL,
            'media_kind', derivative.variant, 'pixel_width', derivative.width,
            'pixel_height', derivative.height
        )
),
-- Keep unmatched people in the windowed pass to avoid joining all people back to their matches.
head_matches AS (
    SELECT person.*, matched.id AS matched_face_id, matched.status AS matched_face_status,
        count(*) OVER (PARTITION BY person.person_id) AS faces_per_person,
        row_number() OVER (PARTITION BY person.person_id) AS person_row,
        count(*) OVER (PARTITION BY matched.id) AS people_per_face
    FROM people person
    LEFT JOIN processing_processingattempt attempt ON attempt.id = person.face_attempt
        AND attempt.input_fingerprint = person.fingerprint AND attempt.accepted
        AND attempt.status = 'succeeded'
    LEFT JOIN LATERAL (
        SELECT face.id, face.status
        FROM processing_photofacedetection face
        CROSS JOIN LATERAL (
            SELECT (face.geometry#>>'{{bbox,0}}')::float8 AS x,
                (face.geometry#>>'{{bbox,1}}')::float8 AS y,
                (face.geometry#>>'{{bbox,2}}')::float8 AS w,
                (face.geometry#>>'{{bbox,3}}')::float8 AS h,
                (person.head->>0)::float8 AS l, (person.head->>1)::float8 AS t,
                (person.head->>2)::float8 AS r, (person.head->>3)::float8 AS b
        ) AS box
        CROSS JOIN LATERAL (
            SELECT greatest(0, least(box.x + box.w, box.r) - greatest(box.x, box.l)) *
                   greatest(0, least(box.y + box.h, box.b) - greatest(box.y, box.t)) AS area
        ) AS overlap
        WHERE face.attempt_id = attempt.id AND box.w > 0 AND box.h > 0
            AND overlap.area >= 0.85 * box.w * box.h
            AND overlap.area >= 0.85 * (box.r - box.l) * (box.b - box.t)
    ) AS matched ON person.head IS NOT NULL
),
resolved_people AS (
    SELECT person_id, photo_id, reason, reid, color, helmet, head_count, face_attempt,
        CASE WHEN faces_per_person = 1 AND people_per_face = 1 THEN matched_face_id END AS face_id,
        matched_face_status
    FROM head_matches
    WHERE person_row = 1
),
linked AS MATERIALIZED (
    SELECT person.*, embedding.vector
    FROM resolved_people person
    LEFT JOIN processing_faceembeddingvector embedding ON embedding.detection_id = person.face_id
        AND person.matched_face_status = 'kept' AND embedding.model_version = %(model)s
),
anchor_input AS (
    SELECT * FROM direct
    WHERE distance <= %(anchor_distance)s AND photo_id IS DISTINCT FROM %(source_photo)s
    UNION ALL SELECT %(source_photo)s::varchar, %(source_detection)s::uuid, 0
    WHERE %(source_photo)s::varchar IS NOT NULL
        AND %(source_detection)s::uuid IS NOT NULL
),
anchor_candidates AS (
    SELECT person.*, anchor_input.distance
    FROM anchor_input
    JOIN linked person ON person.photo_id = anchor_input.photo_id
        AND person.face_id = anchor_input.detection_id
    WHERE person.reid IS NOT NULL AND person.vector IS NOT NULL AND (
        person.photo_id = %(source_photo)s OR (
            person.head_count = 1 AND NOT EXISTS (
                SELECT 1 FROM processing_photofacedetection other
                WHERE other.attempt_id = person.face_attempt AND other.id <> person.face_id
            )
        )
    )
),
first_anchor AS (
    SELECT vector FROM anchor_candidates ORDER BY distance, photo_id, person_id LIMIT 1
),
anchors AS MATERIALIZED (
    SELECT anchor_candidates.* FROM anchor_candidates CROSS JOIN first_anchor
    WHERE (anchor_candidates.vector <=> first_anchor.vector) <= %(anchor_consistency_distance)s
    ORDER BY distance, photo_id, person_id LIMIT %(max_anchors)s
),
nearest AS MATERIALIZED (
    SELECT candidate.person_id, candidate.photo_id, candidate.vector,
        best.person_id AS source_person_id,
        best.photo_id AS source_photo_id,
        best.face_id AS source_detection_id,
        best.reid_cosine_similarity, best.color_cosine_similarity, best.helmet_cosine_similarity
    FROM linked candidate
    CROSS JOIN LATERAL (
        SELECT anchor.person_id, anchor.photo_id, anchor.face_id,
            (1 - (candidate.reid <=> anchor.reid)) AS reid_cosine_similarity,
            (1 - (candidate.color <=> anchor.color)) AS color_cosine_similarity,
            (1 - (candidate.helmet <=> anchor.helmet)) AS helmet_cosine_similarity
        FROM anchors anchor
        WHERE candidate.reid <=> anchor.reid <= 1 - %(reid_cosine_similarity_min)s
        ORDER BY reid_cosine_similarity DESC, anchor.distance, anchor.person_id
        LIMIT 1
    ) AS best
    WHERE candidate.reid IS NOT NULL AND candidate.photo_id IS DISTINCT FROM %(source_photo)s
        AND NOT (candidate.photo_id = ANY(%(baseline)s::varchar[]))
    ORDER BY best.reid_cosine_similarity DESC, candidate.photo_id, candidate.person_id
    LIMIT %(candidate_probe)s
),
assessments AS (
    SELECT person_id, photo_id, source_person_id, source_photo_id, source_detection_id,
        reid_cosine_similarity, color_cosine_similarity, helmet_cosine_similarity,
        CASE WHEN nearest.vector IS NOT NULL THEN greatest(0, least(
            nearest.vector <=> (
                SELECT vector FROM selfie_search_facesearchtask WHERE id = %(task)s
            ),
            (SELECT min(nearest.vector <=> anchor.vector) FROM anchors anchor)
        )) END AS face_cosine_distance
    FROM nearest
),
recommendation_rows AS (
    SELECT person.photo_id, person.person_id, person.face_id AS detection_id, found.result_rank,
        nearest.person_id AS source_person_id, nearest.photo_id AS source_photo_id,
        nearest.face_id AS source_detection_id, nearest.similarity AS reid_cosine_similarity,
        nearest.helmet_similarity AS helmet_cosine_similarity,
        CASE WHEN person.reason <> '' AND person.reason <> 'descriptor_limit' THEN person.reason
             WHEN nearest.similarity < %(reid_cosine_similarity_min)s THEN 'reid_below_threshold'
             WHEN nearest.helmet_similarity < %(helmet_cosine_similarity_min)s THEN 'helmet_veto'
        END AS reason
    FROM found_faces found
    JOIN linked person USING (photo_id)
    LEFT JOIN LATERAL (
        SELECT anchor.person_id, anchor.photo_id, anchor.face_id,
            (1 - (person.reid <=> anchor.reid)) AS similarity,
            (1 - (person.helmet <=> anchor.helmet)) AS helmet_similarity
        FROM anchors anchor
        WHERE anchor.photo_id <> person.photo_id AND person.reid IS NOT NULL
        ORDER BY similarity DESC, anchor.distance, anchor.person_id
        LIMIT 1
    ) AS nearest ON true
    WHERE person.face_id = found.detection_id AND person.photo_id IS DISTINCT FROM %(source_photo)s
),
recommendations AS (
    SELECT * FROM recommendation_rows WHERE reason IS NOT NULL
    ORDER BY result_rank, person_id LIMIT %(recommendation_probe)s
)
SELECT
    COALESCE((SELECT jsonb_agg(to_jsonb(a) ORDER BY
        a.reid_cosine_similarity DESC, a.photo_id, a.person_id)
        FROM assessments a), '[]'::jsonb)::text,
    COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY result_rank, person_id) FROM recommendations r),
        '[]'::jsonb)::text,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'person_id', person_id, 'photo_id', photo_id,
        'detection_id', face_id, 'face_cosine_distance', distance
    ) ORDER BY distance, photo_id, person_id) FROM anchors), '[]'::jsonb)::text
"""


def expand_apparel(
    search: SelfieSearch,
    task_id: int,
    direct: tuple[RankedPhoto, ...],
    expansion: RankedPhotoExpansion,
    *,
    embedding_model: str,
    gallery: bool,
) -> RankedPhotoExpansion:
    import json

    source = search.configuration.get("query_source")
    source_photo = source.get("photo_id") if gallery and isinstance(source, dict) else None
    source_detection = source.get("detection_id") if gallery and isinstance(source, dict) else None
    if gallery and (not isinstance(source_photo, str) or not isinstance(source_detection, str)):
        return expansion
    if source_detection is not None:
        source_detection = UUID(source_detection)

    cohort_sql, cohort_params = (
        retrieval_gallery_photo_queryset(event=search.event)
        .order_by()
        .values("pk")
        .query.sql_with_params()
    )
    # Reuse face-cohort eligibility and bind its positional parameters by name.
    found_faces: list[tuple[str, UUID, int]] = []
    for rank, row in enumerate(expansion.results, start=1):
        detections = {
            *(evidence.member_detection_id for evidence in row.cluster_evidence),
            *(
                match.detection_id
                for match in (row.direct, row.anchor_evidence)
                if match is not None
            ),
        }
        found_faces.extend((row.photo_id, detection, rank) for detection in sorted(detections))
    params: dict[str, Any] = {
        **POLICY,
        "event": search.event_id,
        "model": embedding_model,
        "task": task_id,
        "config": _configuration_hash(APPEARANCE_CONFIGURATION),
        "found_photos": [row[0] for row in found_faces],
        "found_detections": [row[1] for row in found_faces],
        "found_ranks": [row[2] for row in found_faces],
        "ids": [row.photo_id for row in direct],
        "detections": [row.detection_id for row in direct],
        "distances": [row.cosine_distance for row in direct],
        "source_photo": source_photo,
        "source_detection": source_detection,
        "baseline": [row.photo_id for row in expansion.results],
        "candidate_probe": int(POLICY["max_candidates"]) + 1,
        "recommendation_probe": int(POLICY["max_recommendations"]) + 1,
    }
    for index, value in enumerate(cohort_params):
        key = f"cohort_{index}"
        cohort_sql = cohort_sql.replace("%s", f"%({key})s", 1)
        params[key] = value
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute("SET LOCAL log_parameter_max_length_on_error = 0")
        cursor.execute(_QUERY.format(cohort_sql=cohort_sql), params)
        payload = cursor.fetchone()
    assert payload is not None
    candidates, recommendations, anchors = map(json.loads, payload)
    candidate_limit = int(POLICY["max_candidates"])
    candidates_truncated = len(candidates) > candidate_limit
    candidates = candidates[:candidate_limit]
    valid = []
    for candidate in candidates:
        candidate["veto"] = candidate_veto(
            face_distance=candidate["face_cosine_distance"],
            helmet_similarity=candidate["helmet_cosine_similarity"],
        )
        if not candidate["veto"]:
            valid.append(candidate)
    weight = POLICY["reid_rank_weight"]
    valid.sort(
        key=lambda candidate: (
            -(
                weight * candidate["reid_cosine_similarity"]
                + (1 - weight) * candidate["color_cosine_similarity"]
            ),
            candidate["photo_id"],
            candidate["person_id"],
        )
    )
    additions: dict[str, int] = {}
    for candidate in valid:
        additions.setdefault(candidate["photo_id"], candidate["person_id"])
    addition_limit = int(POLICY["max_additions"])
    found = [
        ExpandedRankedPhoto(
            photo_id=photo_id,
            primary_source=str(SelfieSearchResult.PrimarySource.APPAREL_EXPANSION),
            direct=None,
            cluster_evidence=(),
        )
        for photo_id in list(additions)[:addition_limit]
    ]
    recommendation_limit = int(POLICY["max_recommendations"])
    for item in [*candidates, *recommendations, *anchors]:
        for key, value in item.items():
            if isinstance(value, float):
                item[key] = round(value, 6)
    evidence = {
        "policy": POLICY,
        "appearance_configuration_hash": params["config"],
        "appearance_models": APPEARANCE_CONFIGURATION["person_appearance"],
        "anchors": anchors,
        "candidates": candidates,
        "recommendations": recommendations[:recommendation_limit],
        "recommendations_truncated": len(recommendations) > recommendation_limit,
        "candidates_truncated": candidates_truncated,
        "additions_truncated": len(additions) > addition_limit,
        "added_people": {row.photo_id: additions[row.photo_id] for row in found},
    }
    return replace(
        expansion,
        results=expansion.results + tuple(found),
        final_matched_photo_count=expansion.final_matched_photo_count + len(found),
        apparel_expanded_photo_count=len(found),
        apparel_evidence=evidence,
    )
