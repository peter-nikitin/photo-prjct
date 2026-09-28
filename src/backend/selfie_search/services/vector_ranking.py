"""Native exact cosine ranking; a single statement owns the entire cohort snapshot."""

from __future__ import annotations

from time import perf_counter

from django.db import connection
from django.db.models import F
from processing.models import FaceEmbeddingVector
from processing.services.face_cohort import eligible_face_detections
from processing.services.vector_reconciliation import vector_identity_gap_predicates

from selfie_search.models import SelfieSearch
from selfie_search.services.direct_ranking import (
    DirectFaceIdentity,
    DirectRankingOutcome,
    frozen_generations,
)
from selfie_search.services.ranking import (
    RankedPhoto,
    RankingError,
    _configuration,
    validate_query_vector,
)


def rank_vector_direct(
    search: SelfieSearch, query_vector: object, *, comparison_evidence: bool = False
) -> DirectRankingOutcome:
    """Scan all eligible faces, fail closed on gaps, return scalar evidence only.

    PostgreSQL READ COMMITTED supplies a consistent snapshot for this one statement even
    when the caller already has an atomic transaction. No transaction isolation is changed.
    The query vector is a bound parameter and never becomes persistent gallery evidence.
    """
    configuration = _configuration(search)
    query = validate_query_vector(search, query_vector)
    generations = frozen_generations(search)
    missing, divergent = vector_identity_gap_predicates(generations)
    cohort = (
        eligible_face_detections(search.event, generations)
        .annotate(
            detection_id=F("pk"),
            photo_id=F("attempt__photo_id"),
            contract_version=F("attempt__contract_version"),
            processor_version=F("attempt__processor_version"),
            configuration_hash=F("attempt__job__configuration_hash"),
            model_version=F("embedding_vector__model_version"),
            vector_id=F("embedding_vector__pk"),
            missing_vector=missing,
            divergent_vector=divergent,
        )
        .values(
            "detection_id",
            "photo_id",
            "attempt_id",
            "contract_version",
            "processor_version",
            "configuration_hash",
            "model_version",
            "vector_id",
            "missing_vector",
            "divergent_vector",
        )
        .order_by()
    )
    cohort_sql, cohort_params = cohort.query.sql_with_params()
    table = connection.ops.quote_name(FaceEmbeddingVector._meta.db_table)
    sql = f"""
        WITH eligible AS MATERIALIZED ({cohort_sql}),
        counts AS (
            SELECT count(*) AS face_count, count(DISTINCT photo_id) AS photo_count,
                count(*) FILTER (WHERE missing_vector) AS missing_count,
                count(*) FILTER (WHERE divergent_vector) AS divergent_count
            FROM eligible
        ),
        scored AS MATERIALIZED (
            SELECT e.*,
                CASE WHEN c.missing_count = 0 AND c.divergent_count = 0
                    AND v.model_version = %s AND vector_dims(v.vector) = %s
                    THEN LEAST(2.0, GREATEST(0.0, v.vector <=> %s::vector)) END AS distance
            FROM eligible e LEFT JOIN {table} v ON v.id = e.vector_id
            CROSS JOIN counts c
        ),
        ranked AS (
            SELECT scored.*, row_number() OVER (
                PARTITION BY photo_id ORDER BY distance, detection_id
            ) AS photo_rank
            FROM scored
        )
        SELECT c.face_count, c.photo_count, c.missing_count, c.divergent_count,
            e.detection_id, e.photo_id, e.attempt_id, e.contract_version,
            e.processor_version, e.configuration_hash, e.model_version,
            CASE WHEN e.photo_rank = 1 AND (e.distance <= %s OR %s)
                THEN e.distance END,
            e.photo_rank = 1 AND e.distance <= %s AS matched
        FROM counts c LEFT JOIN ranked e ON TRUE
        ORDER BY CASE WHEN e.photo_rank = 1 THEN e.distance END, e.photo_id, e.detection_id
    """
    # Only the validated query travels into PostgreSQL. Gallery vector values never leave it.
    query_literal = "[" + ",".join(str(value) for value in query) + "]"
    started = perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            (
                *cohort_params,
                configuration.model,
                configuration.dimensions,
                query_literal,
                configuration.threshold,
                comparison_evidence,
                configuration.threshold,
            ),
        )
        rows = cursor.fetchall()
    ranking_ms = (perf_counter() - started) * 1_000
    face_count, photo_count, missing_count, divergent_count = rows[0][:4]
    if missing_count or divergent_count:
        raise RankingError("native face cohort is incomplete or divergent")
    snapshot = []
    best_candidates = []
    photos = []
    for row in rows:
        if row[4] is None:
            continue
        snapshot.append(DirectFaceIdentity(*row[4:11]))
        if row[11] is not None:
            ranked = RankedPhoto(photo_id=row[5], detection_id=row[4], cosine_distance=row[11])
            if comparison_evidence:
                best_candidates.append(ranked)
            if row[12]:
                photos.append(ranked)
    return DirectRankingOutcome(
        photos=tuple(photos),
        eligible_face_count=face_count,
        eligible_photo_count=photo_count,
        snapshot=tuple(sorted(snapshot)),
        identity_ms=0.0,
        build_ms=0.0,
        ranking_ms=ranking_ms,
        best_candidates=tuple(best_candidates),
    )
