"""Atomic publication of accepted derivatives into gallery-facing media slots."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import connection, transaction
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
)

from picflow.models import GalleryMediaProjection


class GalleryMediaProjectionConflict(ValueError):
    """A gallery-media slot already contains different accepted evidence."""


@dataclass(frozen=True)
class ProjectionRebuildReport:
    """Aggregate-only projection changes discovered before an optional rebuild."""

    inserted: int
    changed: int
    removed: int


@dataclass(frozen=True)
class ProjectionVerificationReport:
    """Aggregate-only result of the exact expected/actual comparison."""

    clean: bool
    expected_count: int
    projected_count: int
    mismatch_count: int


@dataclass(frozen=True)
class _SqlRelation:
    query: str
    params: tuple[object, ...]


@dataclass(frozen=True)
class _ProjectionSlot:
    final_key_field: str
    source_attempt_field: str
    processor_type: str


_SLOTS = {
    "preview-small-v1": _ProjectionSlot(
        final_key_field="clean_preview_final_key",
        source_attempt_field="clean_preview_source_attempt",
        processor_type=GENERATE_PREVIEW_PROCESSOR,
    ),
    "preview-watermarked-v1": _ProjectionSlot(
        final_key_field="watermarked_preview_final_key",
        source_attempt_field="watermarked_preview_source_attempt",
        processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    ),
}


def _expected_projection_relation() -> _SqlRelation:
    """Return the one canonical PostgreSQL relation for derivable projection rows."""
    quote = connection.ops.quote_name
    derivative_table = quote(PhotoDerivative._meta.db_table)
    attempt_table = quote(ProcessingAttempt._meta.db_table)
    state_table = quote(PhotoProcessingState._meta.db_table)
    return _SqlRelation(
        query=f"""
            SELECT
                derivative.photo_id,
                MAX(derivative.final_key)
                    FILTER (WHERE derivative.variant = %s) AS clean_preview_final_key,
                CAST(
                    MAX(CAST(derivative.accepted_attempt_id AS text))
                        FILTER (WHERE derivative.variant = %s)
                    AS uuid
                ) AS clean_preview_source_attempt_id,
                MAX(derivative.final_key)
                    FILTER (WHERE derivative.variant = %s) AS watermarked_preview_final_key,
                CAST(
                    MAX(CAST(derivative.accepted_attempt_id AS text))
                        FILTER (WHERE derivative.variant = %s)
                    AS uuid
                ) AS watermarked_preview_source_attempt_id
            FROM {derivative_table} AS derivative
            INNER JOIN {attempt_table} AS attempt
                ON attempt.id = derivative.accepted_attempt_id
                AND attempt.photo_id = derivative.photo_id
            INNER JOIN {state_table} AS state
                ON state.photo_id = derivative.photo_id
                AND state.processor_type = attempt.processor_type
                AND state.status = %s
                AND state.current_attempt_id = attempt.id
                AND state.accepted_attempt_id = attempt.id
            WHERE (
                (derivative.variant = %s AND attempt.processor_type = %s)
                OR
                (derivative.variant = %s AND attempt.processor_type = %s)
            )
                AND attempt.status = %s
                AND attempt.accepted
            GROUP BY derivative.photo_id
        """,
        params=(
            "preview-small-v1",
            "preview-small-v1",
            "preview-watermarked-v1",
            "preview-watermarked-v1",
            PhotoProcessingState.Status.SUCCEEDED,
            "preview-small-v1",
            GENERATE_PREVIEW_PROCESSOR,
            "preview-watermarked-v1",
            GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            ProcessingAttempt.Status.SUCCEEDED,
        ),
    )


def _actual_projection_relation() -> str:
    projection_table = connection.ops.quote_name(GalleryMediaProjection._meta.db_table)
    return f"""
        SELECT
            projection.photo_id,
            projection.clean_preview_final_key,
            projection.clean_preview_source_attempt_id,
            projection.watermarked_preview_final_key,
            projection.watermarked_preview_source_attempt_id
        FROM {projection_table} AS projection
    """


def _projection_rebuild_report() -> ProjectionRebuildReport:
    expected = _expected_projection_relation()
    actual = _actual_projection_relation()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                WITH expected_projection AS ({expected.query}),
                actual_projection AS ({actual})
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM expected_projection AS expected
                        LEFT JOIN actual_projection AS actual USING (photo_id)
                        WHERE actual.photo_id IS NULL
                    ) AS inserted,
                    (
                        SELECT COUNT(*)
                        FROM expected_projection AS expected
                        INNER JOIN actual_projection AS actual USING (photo_id)
                        WHERE ROW(
                            expected.clean_preview_final_key,
                            expected.clean_preview_source_attempt_id,
                            expected.watermarked_preview_final_key,
                            expected.watermarked_preview_source_attempt_id
                        ) IS DISTINCT FROM ROW(
                            actual.clean_preview_final_key,
                            actual.clean_preview_source_attempt_id,
                            actual.watermarked_preview_final_key,
                            actual.watermarked_preview_source_attempt_id
                        )
                    ) AS changed,
                    (
                        SELECT COUNT(*)
                        FROM actual_projection AS actual
                        LEFT JOIN expected_projection AS expected USING (photo_id)
                        WHERE expected.photo_id IS NULL
                    ) AS removed
            """,
            expected.params,
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("gallery-media rebuild count query returned no result")
    return ProjectionRebuildReport(
        inserted=int(row[0]),
        changed=int(row[1]),
        removed=int(row[2]),
    )


def rebuild_gallery_media_projection(*, apply: bool) -> ProjectionRebuildReport:
    """Report drift and, only when requested, replace it with expected projection rows."""
    if not apply:
        return _projection_rebuild_report()

    expected = _expected_projection_relation()
    projection_table = connection.ops.quote_name(GalleryMediaProjection._meta.db_table)
    columns = (
        "photo_id",
        "clean_preview_final_key",
        "clean_preview_source_attempt_id",
        "watermarked_preview_final_key",
        "watermarked_preview_source_attempt_id",
    )
    quoted_columns = ", ".join(connection.ops.quote_name(column) for column in columns)
    with transaction.atomic():
        report = _projection_rebuild_report()
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    WITH expected_projection AS ({expected.query})
                    INSERT INTO {projection_table} AS actual (
                        {quoted_columns}, updated_at
                    )
                    SELECT
                        expected.photo_id,
                        expected.clean_preview_final_key,
                        expected.clean_preview_source_attempt_id,
                        expected.watermarked_preview_final_key,
                        expected.watermarked_preview_source_attempt_id,
                        CURRENT_TIMESTAMP
                    FROM expected_projection AS expected
                    ON CONFLICT (photo_id) DO UPDATE SET
                        clean_preview_final_key = EXCLUDED.clean_preview_final_key,
                        clean_preview_source_attempt_id = EXCLUDED.clean_preview_source_attempt_id,
                        watermarked_preview_final_key = EXCLUDED.watermarked_preview_final_key,
                        watermarked_preview_source_attempt_id =
                            EXCLUDED.watermarked_preview_source_attempt_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE ROW(
                        actual.clean_preview_final_key,
                        actual.clean_preview_source_attempt_id,
                        actual.watermarked_preview_final_key,
                        actual.watermarked_preview_source_attempt_id
                    ) IS DISTINCT FROM ROW(
                        EXCLUDED.clean_preview_final_key,
                        EXCLUDED.clean_preview_source_attempt_id,
                        EXCLUDED.watermarked_preview_final_key,
                        EXCLUDED.watermarked_preview_source_attempt_id
                    )
                """,
                expected.params,
            )
            cursor.execute(
                f"""
                    WITH expected_projection AS ({expected.query})
                    DELETE FROM {projection_table} AS actual
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM expected_projection AS expected
                        WHERE expected.photo_id = actual.photo_id
                    )
                """,
                expected.params,
            )
    return report


def verify_gallery_media_projection() -> ProjectionVerificationReport:
    """Compare expected and actual projection tuples without returning row-level facts."""
    expected = _expected_projection_relation()
    actual = _actual_projection_relation()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                WITH expected_projection AS ({expected.query}),
                actual_projection AS ({actual}),
                projection_difference AS (
                    (
                        SELECT * FROM expected_projection
                        EXCEPT
                        SELECT * FROM actual_projection
                    )
                    UNION ALL
                    (
                        SELECT * FROM actual_projection
                        EXCEPT
                        SELECT * FROM expected_projection
                    )
                )
                SELECT
                    (SELECT COUNT(*) FROM expected_projection) AS expected_count,
                    (SELECT COUNT(*) FROM actual_projection) AS projected_count,
                    (SELECT COUNT(*) FROM projection_difference) AS mismatch_count
            """,
            expected.params,
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("gallery-media verification query returned no result")
    mismatch_count = int(row[2])
    return ProjectionVerificationReport(
        clean=mismatch_count == 0,
        expected_count=int(row[0]),
        projected_count=int(row[1]),
        mismatch_count=mismatch_count,
    )


def publish_gallery_media(derivative: PhotoDerivative) -> GalleryMediaProjection:
    """Monotonically publish one current accepted preview derivative."""
    slot = _SLOTS.get(derivative.variant)
    if slot is None:
        raise ValueError("gallery-media projection requires a supported preview derivative")
    if derivative.pk is None:
        raise ValueError("gallery-media projection requires a published derivative")

    supplied_attempt = derivative.accepted_attempt
    if not (
        supplied_attempt.photo_id == derivative.photo_id
        and supplied_attempt.processor_type == slot.processor_type
        and supplied_attempt.status == ProcessingAttempt.Status.SUCCEEDED
        and supplied_attempt.accepted
    ):
        raise ValueError(
            "gallery-media projection requires the matching accepted successful producer"
        )

    with transaction.atomic():
        stored = PhotoDerivative.objects.select_related("accepted_attempt").get(pk=derivative.pk)
        if (
            stored.photo_id != derivative.photo_id
            or stored.variant != derivative.variant
            or stored.final_key != derivative.final_key
            or stored.accepted_attempt_id != derivative.accepted_attempt_id
        ):
            raise ValueError("gallery-media projection requires immutable derivative evidence")

        attempt = stored.accepted_attempt
        state = PhotoProcessingState.objects.filter(
            photo_id=stored.photo_id,
            processor_type=slot.processor_type,
        ).first()
        if not (
            attempt.photo_id == stored.photo_id
            and attempt.processor_type == slot.processor_type
            and attempt.status == ProcessingAttempt.Status.SUCCEEDED
            and attempt.accepted
            and state is not None
            and state.status == PhotoProcessingState.Status.SUCCEEDED
            and state.current_attempt_id == attempt.id
            and state.accepted_attempt_id == attempt.id
        ):
            raise ValueError(
                "gallery-media projection requires the current accepted successful producer"
            )

        projection = (
            GalleryMediaProjection.objects.select_for_update()
            .filter(photo_id=stored.photo_id)
            .first()
        )
        if projection is None:
            projection = GalleryMediaProjection.objects.create(photo_id=stored.photo_id)

        current = (
            getattr(projection, slot.final_key_field),
            getattr(projection, f"{slot.source_attempt_field}_id"),
        )
        published = (stored.final_key, stored.accepted_attempt_id)
        if current == published:
            return projection
        if current != (None, None):
            raise GalleryMediaProjectionConflict(
                "gallery-media projection slot already contains different accepted evidence"
            )

        setattr(projection, slot.final_key_field, stored.final_key)
        setattr(projection, slot.source_attempt_field, attempt)
        projection.save(
            update_fields=[slot.final_key_field, slot.source_attempt_field, "updated_at"]
        )
        return projection
