"""Bounded appearance retrieval using individual accepted person descriptors."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import connection
from picflow.gallery import retrieval_gallery_photo_queryset
from processing.appearance import APPEARANCE_CONFIGURATION
from processing.services.enrollment import _configuration_hash
from psycopg import Cursor

from selfie_search.models import SelfieSearch, SelfieSearchResult
from selfie_search.services.cluster_expansion import ExpandedRankedPhoto, RankedPhotoExpansion
from selfie_search.services.ranking import RankedPhoto

if TYPE_CHECKING:
    from selfie_search.services.continuation import Anchor

POLICY: dict[str, int | float] = {
    "version": 3,
    "anchor_distance": 0.38,
    "anchor_consistency_distance": 0.35,
    "max_anchors": 5,
    "max_anchor_candidates": 20,
    "reid_cosine_similarity_min": 0.70,
    "addition_color_cosine_similarity_min": 0.70,
    "color_cosine_similarity_min": 0.80,
    "helmet_cosine_similarity_min": 0.68,
    "exclusion_reid_cosine_similarity_below": 0.55,
    "ignored_background_area_ratio_max": 0.10,
    "ignored_background_height_ratio_max": 0.35,
    "face_cosine_distance_max": 0.55,
    "reid_rank_weight": 0.8,
    "max_candidates": 200,
    "max_additions": 100,
    "max_recommendations": 100,
}


def candidate_veto(
    *, face_distance: float | None, helmet_similarity: float | None, color_similarity: float
) -> str:
    if face_distance is not None and face_distance > POLICY["face_cosine_distance_max"]:
        return "face_veto"
    if helmet_similarity is not None and helmet_similarity < POLICY["helmet_cosine_similarity_min"]:
        return "helmet_veto"
    if color_similarity < POLICY["addition_color_cosine_similarity_min"]:
        return "color_below_threshold"
    return ""


# The verification pass restricts this reader to the bounded anchor inputs. Retrieval
# scans the event cohort once and reuses the same face-person association for exclusions.
_PEOPLE_CTES = """
appearance_inputs AS MATERIALIZED (
    SELECT attempt.id, state.photo_id, (attempt.result->>'head_count')::int AS head_count,
        jsonb_array_length(attempt.result->'people') AS person_count,
        (attempt.result->>'detections_truncated')::boolean
            OR (attempt.result->>'descriptors_truncated')::boolean AS people_incomplete,
        face_state.accepted_attempt_id AS face_attempt, attempt.input_fingerprint AS fingerprint
    FROM processing_photoprocessingstate state
    JOIN processing_processingattempt attempt ON attempt.id = state.accepted_attempt_id
    JOIN processing_processingjob job ON job.id = attempt.job_id
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
        {photo_scope}
        AND attempt.input_fingerprint = jsonb_build_object(
            'object_key', derivative.final_key, 'object_size', derivative.byte_size,
            'object_content_type', derivative.content_type, 'object_etag', NULL,
            'media_kind', derivative.variant, 'pixel_width', derivative.width,
            'pixel_height', derivative.height
        )
),
people AS MATERIALIZED (
    SELECT appearance.id AS person_id, input.photo_id, appearance.bbox, appearance.head,
        appearance.reason,
        appearance.reid, appearance.color, appearance.helmet, input.head_count,
        input.person_count, input.people_incomplete, input.face_attempt, input.fingerprint
    FROM appearance_inputs input
    JOIN processing_personappearancedetection appearance ON appearance.attempt_id = input.id
),
-- Batch the optional key lookups. The full joins with a subquery boundary prevent
-- a bad row estimate from making PostgreSQL compare every person against the table.
face_attempts AS (
    SELECT id, input_fingerprint
    FROM processing_processingattempt
    WHERE accepted AND status = 'succeeded'
      AND id = ANY(ARRAY(SELECT face_attempt FROM people WHERE face_attempt IS NOT NULL))
),
people_with_attempt AS (
    SELECT mapped.*
    FROM (
        SELECT person.*, CASE WHEN attempt.input_fingerprint = person.fingerprint
            THEN attempt.id END AS matched_attempt_id
        FROM people person
        FULL JOIN face_attempts attempt ON attempt.id = person.face_attempt
        OFFSET 0
    ) mapped
    WHERE person_id IS NOT NULL
),
-- Keep unmatched people in the windowed pass to avoid joining all people back to their matches.
head_matches AS (
    SELECT person.*, matched.id AS matched_face_id, matched.status AS matched_face_status,
        count(*) OVER (PARTITION BY person.person_id) AS faces_per_person,
        row_number() OVER (PARTITION BY person.person_id) AS person_row,
        count(*) OVER (PARTITION BY matched.id) AS people_per_face
    FROM people_with_attempt person
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
        WHERE face.attempt_id = person.matched_attempt_id AND box.w > 0 AND box.h > 0
            AND overlap.area >= 0.85 * box.w * box.h
            AND overlap.area >= 0.85 * (box.r - box.l) * (box.b - box.t)
    ) AS matched ON person.head IS NOT NULL
),
resolved_people AS MATERIALIZED (
    SELECT person_id, photo_id, bbox, reason, reid, color, helmet, head_count, face_attempt,
        person_count, people_incomplete, head IS NOT NULL AS has_assigned_head,
        faces_per_person > 1 OR (matched_face_id IS NOT NULL AND people_per_face > 1)
            AS ambiguous_face,
        CASE WHEN faces_per_person = 1 AND people_per_face = 1 THEN matched_face_id END AS face_id,
        matched_face_status
    FROM head_matches
    WHERE person_row = 1
),
face_vectors AS (
    SELECT detection_id, vector
    FROM processing_faceembeddingvector
    WHERE model_version = %(model)s
      AND detection_id = ANY(ARRAY(
          SELECT face_id FROM resolved_people
          WHERE face_id IS NOT NULL AND matched_face_status = 'kept'
      ))
),
linked AS MATERIALIZED (
    SELECT mapped.*
    FROM (
        SELECT person.*, embedding.vector
        FROM resolved_people person
        FULL JOIN face_vectors embedding ON embedding.detection_id = (
            CASE WHEN person.matched_face_status = 'kept' THEN person.face_id END
        )
        OFFSET 0
    ) mapped
    WHERE person_id IS NOT NULL
)
"""

# The v1 color descriptor concatenates 192-bin head then 192-bin torso histograms.
_VERIFY_QUERY = (
    """
WITH anchor_input AS (
    SELECT * FROM unnest(%(ids)s::varchar[], %(detections)s::uuid[], %(distances)s::float8[])
    AS proposed(photo_id, detection_id, distance)
),
"""
    + _PEOPLE_CTES
    + """,
checks AS MATERIALIZED (
    SELECT proposed.*, p.person_id, p.vector, p.reid, p.color, p.helmet,
        regions.head_color, regions.torso_color,
        CASE WHEN input.id IS NULL THEN 'no_compatible_appearance'
             WHEN p.person_id IS NULL OR p.vector IS NULL THEN 'face_association_unknown'
             WHEN p.reason <> '' THEN p.reason
             WHEN p.reid IS NULL OR p.color IS NULL THEN 'appearance_unknown'
             WHEN vector_norm(regions.head_color) = 0
                OR vector_norm(regions.torso_color) = 0 THEN 'color_region_unknown'
             WHEN p.person_count <> 1 OR p.head_count <> 1 OR p.people_incomplete
                OR EXISTS (
                    SELECT 1 FROM processing_photofacedetection other
                    WHERE other.attempt_id = p.face_attempt AND other.id <> p.face_id
                ) THEN 'multiple_people'
             ELSE '' END AS reason
    FROM anchor_input proposed
    LEFT JOIN appearance_inputs input USING (photo_id)
    LEFT JOIN linked p ON p.photo_id = proposed.photo_id AND p.face_id = proposed.detection_id
    CROSS JOIN LATERAL (
        SELECT subvector(p.color, 1, 192) AS head_color, subvector(p.color, 193, 192) AS torso_color
    ) regions
),
comparisons AS (
    SELECT a.photo_id AS left_photo_id, b.photo_id AS right_photo_id,
        greatest(0, a.vector <=> b.vector) AS face_cosine_distance,
        1 - (a.reid <=> b.reid) AS reid_cosine_similarity,
        1 - (a.color <=> b.color) AS color_cosine_similarity,
        1 - (a.head_color <=> b.head_color) AS head_color_cosine_similarity,
        1 - (a.torso_color <=> b.torso_color) AS torso_color_cosine_similarity,
        1 - (a.helmet <=> b.helmet) AS helmet_cosine_similarity
    FROM checks a JOIN checks b ON a.photo_id < b.photo_id
    WHERE a.reason = '' AND b.reason = ''
)
SELECT
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'photo_id', photo_id, 'detection_id', detection_id, 'person_id', person_id,
        'face_cosine_distance', distance, 'reason', reason
    ) ORDER BY distance, photo_id, detection_id) FROM checks), '[]'::jsonb)::text,
    COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY left_photo_id, right_photo_id)
        FROM comparisons c), '[]'::jsonb)::text
"""
)


_QUERY = (
    """
WITH face_queries AS MATERIALIZED (
    SELECT vector FROM selfie_search_facesearchtask WHERE id = %(task)s
        AND cardinality(%(query_detection_ids)s::uuid[]) = 0
    UNION ALL
    SELECT l2_normalize(avg(l2_normalize(embedding.vector))) AS vector
    FROM unnest(%(query_detection_ids)s::uuid[], %(query_cen_indices)s::smallint[])
        AS source(detection_id, centroid_index)
    JOIN processing_faceembeddingvector embedding USING (detection_id)
    GROUP BY source.centroid_index
),
found_faces AS (
    SELECT * FROM unnest(%(found_photos)s::varchar[], %(found_detections)s::uuid[],
        %(found_ranks)s::int[], %(found_centroid_indices)s::smallint[])
        AS found(photo_id, detection_id, result_rank, centroid_index)
),
found_centroids AS (
    SELECT DISTINCT photo_id, centroid_index FROM found_faces WHERE centroid_index IS NOT NULL
),
"""
    + _PEOPLE_CTES
    + """,
anchors AS MATERIALIZED (
    SELECT person.*, selected.ordinality AS position, selected.centroid_index,
        subvector(person.color, 1, 192) AS head_color,
        subvector(person.color, 193, 192) AS torso_color
    FROM unnest(%(anchor_person_ids)s::bigint[], %(anchor_detection_ids)s::uuid[],
        %(anchor_centroid_indices)s::smallint[])
        WITH ORDINALITY AS selected(person_id, detection_id, centroid_index, ordinality)
    JOIN linked person ON person.person_id = selected.person_id
        AND person.face_id = selected.detection_id
    WHERE person.vector IS NOT NULL AND person.reid IS NOT NULL AND person.color IS NOT NULL
),
nearest AS MATERIALIZED (
    SELECT candidate.person_id, candidate.photo_id, candidate.vector, candidate.face_id,
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
        ORDER BY reid_cosine_similarity DESC, anchor.position
        LIMIT 1
    ) AS best
    WHERE candidate.reason = '' AND candidate.reid IS NOT NULL AND candidate.color IS NOT NULL
        AND candidate.photo_id IS DISTINCT FROM %(source_photo)s
        AND NOT (candidate.photo_id = ANY(%(baseline)s::varchar[]))
        AND NOT (candidate.photo_id = ANY(%(prior_photos)s::varchar[]))
    ORDER BY best.reid_cosine_similarity DESC, candidate.photo_id, candidate.person_id
    LIMIT %(candidate_probe)s
),
assessments AS (
    SELECT person_id, photo_id, face_id AS detection_id,
        source_person_id, source_photo_id, source_detection_id,
        reid_cosine_similarity, color_cosine_similarity, helmet_cosine_similarity,
        CASE WHEN nearest.vector IS NOT NULL THEN greatest(0, least(
            (SELECT min(nearest.vector <=> vector) FROM face_queries),
            (SELECT min(nearest.vector <=> anchor.vector) FROM anchors anchor)
        )) END AS face_cosine_distance
    FROM nearest
),
person_assessments AS MATERIALIZED (
    SELECT person.photo_id, person.person_id, person.face_id, person.reason, person.bbox,
        dimensions.width * dimensions.height AS area, dimensions.height,
        person.head_count, person.has_assigned_head, person.people_incomplete,
        person.ambiguous_face, person.vector IS NOT NULL AS face_verified,
        person.reid IS NOT NULL AND person.color IS NOT NULL AS has_descriptors,
        coalesce(c.agrees, false) AS agrees, c.comparisons,
        coalesce(person.reason = ''
            AND c.highest_reid < %(exclusion_reid_cosine_similarity_below)s
            AND c.highest_color < %(color_cosine_similarity_min)s
            AND c.torso_disagrees, false) AS confident_mismatch
    FROM linked person
    LEFT JOIN found_centroids centroid USING (photo_id)
    CROSS JOIN LATERAL (
        SELECT (person.bbox->>2)::float8 - (person.bbox->>0)::float8 AS width,
            (person.bbox->>3)::float8 - (person.bbox->>1)::float8 AS height
    ) dimensions
    CROSS JOIN LATERAL (
        SELECT subvector(person.color, 1, 192) AS head_color,
            subvector(person.color, 193, 192) AS torso_color
    ) regions
    CROSS JOIN LATERAL (
        SELECT max(sim.reid) AS highest_reid, max(sim.color) AS highest_color,
            bool_and(coalesce(sim.torso_color < %(color_cosine_similarity_min)s, false))
                AS torso_disagrees,
            bool_or(sim.reid >= %(reid_cosine_similarity_min)s
                AND sim.color >= %(color_cosine_similarity_min)s
                AND (sim.helmet IS NULL OR sim.helmet >= %(helmet_cosine_similarity_min)s)
            ) AS agrees,
            jsonb_agg(jsonb_build_object(
                'anchor_position', anchor.position,
                'photo_id', anchor.photo_id, 'person_id', anchor.person_id,
                'detection_id', anchor.face_id, 'face_cosine_distance',
                    CASE WHEN person.vector IS NOT NULL
                        THEN greatest(0, person.vector <=> anchor.vector) END,
                'reid_cosine_similarity', sim.reid, 'color_cosine_similarity', sim.color,
                'head_color_cosine_similarity', sim.head_color,
                'torso_color_cosine_similarity', sim.torso_color,
                'helmet_cosine_similarity', sim.helmet
            ) ORDER BY sim.reid DESC NULLS LAST, anchor.position) AS comparisons
        FROM anchors anchor
        CROSS JOIN LATERAL (
            SELECT 1 - (person.reid <=> anchor.reid) AS reid,
                1 - (person.color <=> anchor.color) AS color,
                CASE WHEN vector_norm(regions.head_color) > 0
                    AND vector_norm(anchor.head_color) > 0
                    THEN 1 - (regions.head_color <=> anchor.head_color) END AS head_color,
                CASE WHEN vector_norm(regions.torso_color) > 0
                    AND vector_norm(anchor.torso_color) > 0
                    THEN 1 - (regions.torso_color <=> anchor.torso_color) END AS torso_color,
                1 - (person.helmet <=> anchor.helmet) AS helmet
        ) sim
        WHERE centroid.centroid_index IS NULL OR anchor.centroid_index = centroid.centroid_index
    ) c
    WHERE person.photo_id = ANY(%(baseline)s::varchar[])
        AND person.photo_id IS DISTINCT FROM %(source_photo)s
),
face_assessments AS MATERIALIZED (
    SELECT found.photo_id, found.detection_id, found.result_rank, p.person_id,
        coalesce(p.confident_mismatch AND p.face_verified, false) AS confident_mismatch,
        p.comparisons,
        CASE WHEN p.reason <> '' AND p.reason <> 'descriptor_limit' THEN p.reason
             WHEN (p.comparisons->0->>'reid_cosine_similarity')::float8 <
                    %(reid_cosine_similarity_min)s THEN 'reid_below_threshold'
             WHEN (p.comparisons->0->>'helmet_cosine_similarity')::float8 <
                    %(helmet_cosine_similarity_min)s THEN 'helmet_veto'
        END AS reason
    FROM found_faces found
    LEFT JOIN person_assessments p
        ON p.photo_id = found.photo_id AND p.face_id = found.detection_id
    WHERE found.photo_id IS DISTINCT FROM %(source_photo)s
),
background_references AS (
    SELECT DISTINCT ON (f.photo_id) f.photo_id, p.person_id, f.detection_id, p.bbox,
        p.area, p.height
    FROM face_assessments f
    JOIN person_assessments p USING (person_id)
    WHERE p.reason = '' AND p.face_verified AND p.has_descriptors
    ORDER BY f.photo_id, p.area DESC, p.person_id
),
group_assessments AS (
    SELECT p.photo_id,
        CASE WHEN bool_or(p.agrees) THEN 'agreeing_figure'
             WHEN max(p.head_count) > count(*) FILTER (WHERE p.has_assigned_head)
                OR bool_or(p.people_incomplete OR p.ambiguous_face
                    OR (NOT p.confident_mismatch AND NOT background.ignored))
                 THEN 'uncertain_figure'
             ELSE '' END AS protection,
        coalesce(jsonb_agg(jsonb_build_object(
            'person_id', p.person_id, 'reason', p.reason, 'bbox', p.bbox,
            'area_ratio', ratios.area, 'height_ratio', ratios.height,
            'reference_person_id', ref.person_id,
            'reference_detection_id', ref.detection_id, 'reference_bbox', ref.bbox,
            'decision_reason', 'small_headless_quality_rejection'
        ) ORDER BY p.person_id) FILTER (WHERE background.ignored), '[]'::jsonb)
            AS ignored_background_people
    FROM person_assessments p
    LEFT JOIN background_references ref USING (photo_id)
    CROSS JOIN LATERAL (
        SELECT p.area / ref.area AS area, p.height / ref.height AS height
    ) ratios
    CROSS JOIN LATERAL (
        SELECT coalesce(p.reason IN ('lack_details', 'strong_blur') AND NOT p.has_assigned_head
            AND NOT p.ambiguous_face
            AND ratios.area <= %(ignored_background_area_ratio_max)s
            AND ratios.height <= %(ignored_background_height_ratio_max)s, false) AS ignored
    ) background
    GROUP BY p.photo_id
),
recommendations AS (
    SELECT f.*, g.protection, g.ignored_background_people,
        f.comparisons->0->>'photo_id' AS source_photo_id,
        f.comparisons->0->'person_id' AS source_person_id,
        f.comparisons->0->>'detection_id' AS source_detection_id,
        f.comparisons->0->'reid_cosine_similarity' AS reid_cosine_similarity,
        f.comparisons->0->'color_cosine_similarity' AS color_cosine_similarity,
        f.comparisons->0->'helmet_cosine_similarity' AS helmet_cosine_similarity
    FROM face_assessments f LEFT JOIN group_assessments g USING (photo_id)
    WHERE f.reason IS NOT NULL
    ORDER BY f.result_rank, f.detection_id LIMIT %(recommendation_probe)s
),
exclusions AS (
    SELECT f.photo_id, f.result_rank, 'all_matched_figures_disagree' AS reason,
        jsonb_agg(jsonb_build_object(
            'detection_id', f.detection_id, 'person_id', f.person_id, 'comparisons', f.comparisons
        ) ORDER BY f.detection_id) AS matches, g.ignored_background_people
    FROM face_assessments f JOIN group_assessments g USING (photo_id)
    WHERE g.protection = ''
    GROUP BY f.photo_id, f.result_rank, g.ignored_background_people
    HAVING bool_and(f.confident_mismatch)
)
SELECT
    COALESCE((SELECT jsonb_agg(to_jsonb(a) ORDER BY
        a.reid_cosine_similarity DESC, a.photo_id, a.person_id)
        FROM assessments a), '[]'::jsonb)::text,
    COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.result_rank, r.detection_id)
        FROM recommendations r), '[]'::jsonb)::text,
    COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.result_rank)
        FROM exclusions e), '[]'::jsonb)::text,
    (SELECT count(*) FROM anchors)
"""
)


@dataclass(frozen=True, slots=True)
class ApparelAnchorVerification:
    source_photo_id: str | None
    outcome: str
    anchors: tuple[dict[str, Any], ...]
    checks: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    truncated: bool
    group_outcomes: tuple[str, ...] = ()

    @property
    def centroid_detection_ids(self) -> tuple[UUID, ...] | None:
        if self.outcome == "no_compatible_appearance":
            return None
        return tuple(
            UUID(row["detection_id"])
            for row in self.anchors
            if row["photo_id"] != self.source_photo_id
        )


def _read_appearance_sql(
    search: SelfieSearch,
    statement: str,
    params: dict[str, Any],
    *,
    photo_scope: str = "",
) -> tuple[Any, ...]:
    cohort_sql, cohort_params = (
        retrieval_gallery_photo_queryset(event=search.event)
        .order_by()
        .values("pk")
        .query.sql_with_params()
    )
    # Reuse face-cohort eligibility and bind its positional parameters by name.
    for index, value in enumerate(cohort_params):
        key = f"cohort_{index}"
        cohort_sql = cohort_sql.replace("%s", f"%({key})s", 1)
        params[key] = value
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        cursor.execute("SET LOCAL log_parameter_max_length_on_error = 0")
        cursor.execute(statement.format(cohort_sql=cohort_sql, photo_scope=photo_scope), params)
        row = cursor.fetchone()
        assert row is not None
        return row


def _compatible(comparison: dict[str, Any]) -> bool:
    helmet = comparison["helmet_cosine_similarity"]
    return (
        comparison["face_cosine_distance"] <= POLICY["anchor_consistency_distance"]
        and comparison["reid_cosine_similarity"] >= POLICY["reid_cosine_similarity_min"]
        and min(
            comparison["color_cosine_similarity"],
            comparison["head_color_cosine_similarity"],
            comparison["torso_color_cosine_similarity"],
        )
        >= POLICY["color_cosine_similarity_min"]
        and (helmet is None or helmet >= POLICY["helmet_cosine_similarity_min"])
    )


def _select_anchors(
    checks: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    source_photo_id: str | None,
) -> tuple[str, tuple[str, ...]]:
    if all(row["reason"] == "no_compatible_appearance" for row in checks):
        return "no_compatible_appearance", ()

    candidates = [row["photo_id"] for row in checks if not row["reason"]]
    pairs = {
        frozenset((row["left_photo_id"], row["right_photo_id"]))
        for row in comparisons
        if _compatible(row)
    }

    def supported_group(seed: str) -> tuple[str, ...]:
        group = [seed]
        for candidate in candidates:
            if candidate != seed and all(
                frozenset((candidate, member)) in pairs for member in group
            ):
                group.append(candidate)
        return tuple(photo_id for photo_id in candidates if photo_id in group)

    limit = int(POLICY["max_anchors"])
    if source_photo_id is not None:
        if source_photo_id not in candidates:
            return "reference_unassessable", ()
        group = supported_group(source_photo_id)
        return "verified", (
            source_photo_id,
            *(photo_id for photo_id in group if photo_id != source_photo_id),
        )[:limit]

    groups = {supported_group(candidate) for candidate in candidates}
    largest = max(map(len, groups), default=0)
    if largest < 2:
        return "insufficient_agreement", ()
    leaders = [group for group in groups if len(group) == largest]
    if len(leaders) != 1:
        return "ambiguous_agreement", ()
    return "verified", leaders[0][:limit]


def _read_anchor_checks(
    search: SelfieSearch, proposed: list[tuple[str, UUID, float]], model: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks, comparisons = _read_appearance_sql(
        search,
        _VERIFY_QUERY,
        {
            "event": search.event_id,
            "model": model,
            "config": _configuration_hash(APPEARANCE_CONFIGURATION),
            "ids": [row[0] for row in proposed],
            "detections": [row[1] for row in proposed],
            "distances": [row[2] for row in proposed],
        },
        photo_scope="AND state.photo_id IN (SELECT photo_id FROM anchor_input)",
    )
    return json.loads(checks), json.loads(comparisons)


@dataclass(frozen=True, slots=True)
class ContinuationApparelChecks:
    checks: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]

    def compatible_neighbors(self) -> dict[str, set[str]]:
        unindexed = {
            row["photo_id"] for row in self.checks if row["reason"] == "no_compatible_appearance"
        }
        neighbors = {photo_id: unindexed - {photo_id} for photo_id in unindexed}
        neighbors.update({row["photo_id"]: set() for row in self.checks if not row["reason"]})
        for comparison in self.comparisons:
            if _compatible(comparison):
                left, right = comparison["left_photo_id"], comparison["right_photo_id"]
                neighbors[left].add(right)
                neighbors[right].add(left)
        return neighbors


def check_continuation_apparel(
    search: SelfieSearch, candidates: tuple[Anchor, ...], model: str
) -> ContinuationApparelChecks:
    return ContinuationApparelChecks(
        *_read_anchor_checks(
            search, [(anchor.photo_id, anchor.detection_id, 0.0) for anchor in candidates], model
        )
    )


def _select_continuation_anchors(
    groups: tuple[tuple[Anchor, ...], ...],
    checks: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> tuple[tuple[str, ...], dict[str, int]]:
    by_photo = {row["photo_id"]: row for row in checks}
    outcomes = []
    selected = {}
    for position, group in enumerate(groups, start=1):
        group_checks = [by_photo[anchor.photo_id] for anchor in group]
        reference = next((row["photo_id"] for row in group_checks if not row["reason"]), None)
        if reference is None:
            outcome = (
                "no_compatible_appearance"
                if all(row["reason"] == "no_compatible_appearance" for row in group_checks)
                else "reference_unassessable"
            )
            outcomes.append(outcome)
            continue
        outcome, photo_ids = _select_anchors(group_checks, comparisons, reference)
        outcomes.append(outcome)
        selected.update(dict.fromkeys(photo_ids, position))
    return tuple(outcomes), selected


def verify_apparel_anchors(
    search: SelfieSearch,
    direct: tuple[RankedPhoto, ...],
    *,
    embedding_model: str,
    gallery: bool,
    continuation_groups: tuple[tuple[Anchor, ...], ...] = (),
    continuation_checks: ContinuationApparelChecks | None = None,
) -> ApparelAnchorVerification:
    source = search.configuration.get("query_source")
    source_photo_id = None
    proposed: list[tuple[str, UUID, float]] = []
    if gallery:
        source_photo = source.get("photo_id") if isinstance(source, dict) else None
        source_detection = source.get("detection_id") if isinstance(source, dict) else None
        if not isinstance(source_photo, str) or not isinstance(source_detection, str):
            raise ValueError("gallery appearance search requires a source detection")
        source_photo_id = source_photo
        proposed.append((source_photo, UUID(source_detection), 0.0))
    candidates = [
        row
        for row in direct
        if not continuation_groups
        and row.cosine_distance <= POLICY["anchor_distance"]
        and row.photo_id != source_photo_id
    ]
    limit = int(POLICY["max_anchor_candidates"])
    proposed.extend(
        (row.photo_id, row.detection_id, row.cosine_distance) for row in candidates[:limit]
    )
    if continuation_groups:
        if continuation_checks is None:
            raise ValueError("missing apparel checks for continuation anchors")
        checked = {row["photo_id"]: row for row in continuation_checks.checks}
        checks = [
            dict(checked[anchor.photo_id]) for group in continuation_groups for anchor in group
        ]
        selected_ids = {row["photo_id"] for row in checks}
        comparisons = [
            dict(row)
            for row in continuation_checks.comparisons
            if row["left_photo_id"] in selected_ids and row["right_photo_id"] in selected_ids
        ]
    else:
        checks, comparisons = _read_anchor_checks(search, proposed, embedding_model)
    group_outcomes: tuple[str, ...] = ()
    centroid_by_photo: dict[str, int] = {}
    if continuation_groups:
        group_outcomes, centroid_by_photo = _select_continuation_anchors(
            continuation_groups, checks, comparisons
        )
        outcome = (
            "reference_unassessable"
            if "reference_unassessable" in group_outcomes
            else "verified"
            if centroid_by_photo
            else "no_compatible_appearance"
        )
        selected_photo_ids = tuple(centroid_by_photo)
    else:
        outcome, selected_photo_ids = _select_anchors(checks, comparisons, source_photo_id)
    selected = set(selected_photo_ids)
    by_photo = {row["photo_id"]: row for row in checks}
    result_rank = {row.photo_id: rank for rank, row in enumerate(direct, start=1)}
    for position, row in enumerate(checks, start=1):
        row["check_position"] = position
        row["result_rank"] = result_rank.get(row["photo_id"])
    for comparison in comparisons:
        comparison["left_check_position"] = by_photo[comparison["left_photo_id"]]["check_position"]
        comparison["right_check_position"] = by_photo[comparison["right_photo_id"]][
            "check_position"
        ]
    selected_pairs = {
        frozenset((row["left_photo_id"], row["right_photo_id"]))
        for row in comparisons
        if not _compatible(row)
    }
    for row in checks:
        photo_id = row["photo_id"]
        if photo_id in selected:
            row["decision"] = "accepted"
        elif row["reason"]:
            row["decision"] = "unknown"
        elif any(frozenset((photo_id, chosen)) in selected_pairs for chosen in selected):
            row.update(decision="rejected", reason="incompatible_features")
        else:
            row.update(decision="not_selected", reason=outcome if not selected else "anchor_limit")
    anchors = tuple(
        {
            key: value
            for key, value in by_photo[photo_id].items()
            if key not in ("decision", "reason")
        }
        | {"anchor_position": position}
        | ({"centroid_index": centroid_by_photo[photo_id]} if continuation_groups else {})
        for position, photo_id in enumerate(selected_photo_ids, start=1)
    )
    return ApparelAnchorVerification(
        source_photo_id,
        outcome,
        anchors,
        checks,
        comparisons,
        len(candidates) > limit,
        group_outcomes,
    )


def expand_apparel(
    search: SelfieSearch,
    task_id: int,
    expansion: RankedPhotoExpansion,
    *,
    embedding_model: str,
    verification: ApparelAnchorVerification,
    continuation_groups: tuple[tuple[Anchor, ...], ...] = (),
    prior_photo_ids: tuple[str, ...] = (),
) -> RankedPhotoExpansion:
    config_hash = _configuration_hash(APPEARANCE_CONFIGURATION)
    candidates, recommendations, exclusions = [], [], []
    if verification.anchors:
        found_faces: list[tuple[str, UUID, int, int | None]] = []
        for rank, row in enumerate(expansion.results, start=1):
            detections = {
                *(evidence.member_detection_id for evidence in row.cluster_evidence),
                *(
                    match.detection_id
                    for match in (row.direct, row.anchor_evidence)
                    if match is not None
                ),
            }
            centroid_index = row.direct.centroid_index if row.direct is not None else None
            found_faces.extend(
                (row.photo_id, detection, rank, centroid_index) for detection in sorted(detections)
            )
        query_members = [
            (anchor.detection_id, index)
            for index, group in enumerate(continuation_groups, start=1)
            for anchor in group
        ]
        *payload, anchors_count = _read_appearance_sql(
            search,
            _QUERY,
            {
                **POLICY,
                "event": search.event_id,
                "model": embedding_model,
                "task": task_id,
                "query_detection_ids": [member[0] for member in query_members],
                "query_cen_indices": [member[1] for member in query_members],
                "config": config_hash,
                "found_photos": [row[0] for row in found_faces],
                "found_detections": [row[1] for row in found_faces],
                "found_ranks": [row[2] for row in found_faces],
                "found_centroid_indices": [row[3] for row in found_faces],
                "source_photo": verification.source_photo_id,
                "anchor_person_ids": [row["person_id"] for row in verification.anchors],
                "anchor_detection_ids": [UUID(row["detection_id"]) for row in verification.anchors],
                "anchor_centroid_indices": [
                    row.get("centroid_index") for row in verification.anchors
                ],
                "baseline": [row.photo_id for row in expansion.results],
                "prior_photos": list(prior_photo_ids),
                "candidate_probe": int(POLICY["max_candidates"]) + 1,
                "recommendation_probe": int(POLICY["max_recommendations"]) + 1,
            },
        )
        if anchors_count != len(verification.anchors):
            raise ValueError("verified appearance anchors changed during matching")
        candidates, recommendations, exclusions = map(json.loads, payload)
    candidate_limit = int(POLICY["max_candidates"])
    candidates_truncated = len(candidates) > candidate_limit
    candidates = candidates[:candidate_limit]
    valid = []
    for candidate in candidates:
        candidate["veto"] = candidate_veto(
            face_distance=candidate["face_cosine_distance"],
            helmet_similarity=candidate["helmet_cosine_similarity"],
            color_similarity=candidate["color_cosine_similarity"],
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
    additions: dict[str, tuple[int, UUID | None]] = {}
    for candidate in valid:
        detection_id = candidate.get("detection_id")
        additions.setdefault(
            candidate["photo_id"],
            (candidate["person_id"], UUID(detection_id) if detection_id else None),
        )
    addition_limit = int(POLICY["max_additions"])
    found = [
        ExpandedRankedPhoto(
            photo_id=photo_id,
            primary_source=str(SelfieSearchResult.PrimarySource.APPAREL_EXPANSION),
            direct=None,
            cluster_evidence=(),
            apparel_detection_id=additions[photo_id][1],
        )
        for photo_id in list(additions)[:addition_limit]
    ]
    recommendation_limit = int(POLICY["max_recommendations"])
    for item in candidates:
        for key, value in item.items():
            if isinstance(value, float):
                item[key] = round(value, 6)
    evidence = {
        "policy": POLICY,
        "appearance_configuration_hash": config_hash,
        "appearance_models": APPEARANCE_CONFIGURATION["person_appearance"],
        "anchor_outcome": verification.outcome,
        "anchor_checks": verification.checks,
        "anchor_comparisons": verification.comparisons,
        "anchor_candidates_truncated": verification.truncated,
        "anchors": verification.anchors,
        "candidates": candidates,
        "exclusions": exclusions,
        "recommendations": recommendations[:recommendation_limit],
        "recommendations_truncated": len(recommendations) > recommendation_limit,
        "candidates_truncated": candidates_truncated,
        "additions_truncated": len(additions) > addition_limit,
        "added_people": {row.photo_id: additions[row.photo_id][0] for row in found},
    }
    if continuation_groups:
        evidence["centroid_anchor_outcomes"] = verification.group_outcomes
    return replace(
        expansion,
        results=expansion.results + tuple(found),
        final_matched_photo_count=expansion.final_matched_photo_count + len(found),
        apparel_expanded_photo_count=len(found),
        apparel_evidence=evidence,
        excluded_photo_ids=frozenset(row["photo_id"] for row in exclusions),
    )
