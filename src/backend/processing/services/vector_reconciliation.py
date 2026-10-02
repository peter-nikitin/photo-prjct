"""Bounded private operator reconciliation of immutable historical face evidence."""

from __future__ import annotations

import struct
from collections.abc import Iterator, Mapping, Sequence
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Count, F, Q
from picflow.models import Event

from processing.models import FaceEmbedding, FaceEmbeddingVector
from processing.services.face_cohort import eligible_face_detections
from processing.services.vector_embeddings import (
    generation_uses_vector_only_storage,
    non_vector_metadata,
    validate_embedding,
    vector_values,
)


def converted_vector(values: list[float]) -> list[float]:
    """Match pgvector's stored float32 values exactly, rather than a loose tolerance."""
    return [struct.unpack("f", struct.pack("f", value))[0] for value in values]


def _agrees(source: FaceEmbedding, target: FaceEmbeddingVector, values: list[float]) -> bool:
    return (
        target.model_version == source.model_version
        and converted_vector(vector_values(target.vector)) == converted_vector(values)
        and target.metadata == non_vector_metadata(source.metadata)
    )


def backfill_embeddings(
    *,
    apply: bool = False,
    batch_size: int = 500,
    max_rows: int = 5000,
    after: UUID | None = None,
    event: Event | None = None,
) -> dict[str, Any]:
    """Commit independently bounded batches; report a UUID cursor only to the operator.

    Detection uniqueness reconciles a callback inserting between selection and publication.
    Existing evidence is checked and never updated, even if it disagrees with the source.
    """
    if not 1 <= batch_size <= 1000 or not 1 <= max_rows <= 50000:
        raise ValueError("batch size must be 1–1000; maximum rows must be 1–50000")
    report: dict[str, Any] = dict(
        apply=apply,
        scanned=0,
        created=0,
        existing=0,
        missing=0,
        invalid=0,
        divergent=0,
        batches=0,
        cursor=str(after) if after else None,
    )
    sources = FaceEmbedding.objects.filter(
        detection__status="kept",
        detection__attempt__status="succeeded",
        detection__attempt__accepted=True,
    )
    if event is not None:
        sources = sources.filter(detection__attempt__event=event)
    cursor = after
    while report["scanned"] < max_rows:
        pending = sources.filter(pk__gt=cursor) if cursor else sources
        batch = list(pending.order_by("pk")[: min(batch_size, max_rows - report["scanned"])])
        if not batch:
            break
        with transaction.atomic():
            for source in batch:
                report["scanned"] += 1
                try:
                    values = validate_embedding(source.vector, model_version=source.model_version)
                except ValueError:
                    report["invalid"] += 1
                    continue
                if apply:
                    target, created = FaceEmbeddingVector.objects.get_or_create(
                        detection_id=source.detection_id,
                        defaults=dict(
                            model_version=source.model_version,
                            vector=values,
                            metadata=non_vector_metadata(source.metadata),
                        ),
                    )
                    if created:
                        report["created"] += 1
                    elif _agrees(source, target, values):
                        report["existing"] += 1
                    else:
                        report["divergent"] += 1
                else:
                    target = FaceEmbeddingVector.objects.filter(
                        detection_id=source.detection_id
                    ).first()
                    if target is None:
                        report["missing"] += 1
                    elif _agrees(source, target, values):
                        report["existing"] += 1
                    else:
                        report["divergent"] += 1
        cursor = batch[-1].pk
        report["cursor"] = str(cursor)
        report["batches"] += 1
    report["has_more"] = sources.filter(pk__gt=cursor).exists() if cursor else sources.exists()
    return report


def iter_reconciliation_rows(
    event: Event | None = None,
    generations: Sequence[Mapping[str, object]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Both representations and their scalar identity in one SQL statement/snapshot.

    Rows stay private to validation; callers must never log their payload. Frozen native
    generations may omit JSON evidence; every other generation still requires parallel proof.
    """
    yield from (
        eligible_face_detections(event, generations)
        .values(
            "id",
            "attempt_id",
            "attempt__event_id",
            "attempt__contract_version",
            "attempt__processor_version",
            "attempt__processor_type",
            "attempt__configuration",
            "attempt__job__configuration",
            "attempt__run__configuration",
            "attempt__job__configuration_hash",
            "embedding__id",
            "embedding__model_version",
            "embedding__vector",
            "embedding__metadata",
            "embedding_vector__id",
            "embedding_vector__model_version",
            "embedding_vector__vector",
            "embedding_vector__metadata",
        )
        .iterator(chunk_size=1000)
    )


def verify_embeddings(
    event: Event | None = None,
    generations: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Validate complete eligible identities/values and return only aggregate diagnostics."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    expected_models = {
        (
            generation["contract_version"],
            generation["processor_version"],
            generation["configuration_hash"],
        ): generation["model"]
        for generation in generations or ()
    }
    for row in iter_reconciliation_rows(event, generations):
        configuration = row["attempt__job__configuration"]
        frozen_model = configuration.get("face_embedding", {}).get("model")
        model = (
            row["embedding__model_version"]
            or row["embedding_vector__model_version"]
            or frozen_model
            or "unknown"
        )
        key = str(row["attempt__event_id"]), model
        group = groups.setdefault(
            key, dict(event=key[0], model=model, eligible=0, missing=0, divergent=0, invalid=0)
        )
        group["eligible"] += 1
        expected_model = expected_models.get(
            (
                row["attempt__contract_version"],
                row["attempt__processor_version"],
                row["attempt__job__configuration_hash"],
            )
        )
        if expected_model is not None and model != expected_model:
            group["invalid"] += 1
            continue
        generation = {
            "contract_version": row["attempt__contract_version"],
            "processor_type": row["attempt__processor_type"],
            "processor_version": row["attempt__processor_version"],
            "configuration": configuration,
            "configuration_hash": row["attempt__job__configuration_hash"],
            "model": frozen_model,
        }
        try:
            native = generation_uses_vector_only_storage(generation)
        except ValueError:
            group["invalid"] += 1
            continue
        if native and (
            row["attempt__configuration"] != configuration
            or row["attempt__run__configuration"] != configuration
        ):
            group["invalid"] += 1
            continue
        if native and row["embedding__id"] is None:
            if row["embedding_vector__id"] is None:
                group["missing"] += 1
            elif model != frozen_model:
                group["invalid"] += 1
            else:
                try:
                    validate_embedding(
                        vector_values(row["embedding_vector__vector"]), model_version=model
                    )
                except ValueError:
                    group["invalid"] += 1
            continue
        try:
            values = validate_embedding(row["embedding__vector"], model_version=model)
        except ValueError:
            group["invalid"] += 1
            continue
        if row["embedding_vector__id"] is None:
            group["missing"] += 1
        elif (
            row["embedding_vector__model_version"] != model
            or converted_vector(vector_values(row["embedding_vector__vector"]))
            != converted_vector(values)
            or row["embedding_vector__metadata"] != non_vector_metadata(row["embedding__metadata"])
        ):
            group["divergent"] += 1
    historical = FaceEmbedding.objects.all()
    if event is not None:
        historical = historical.filter(detection__attempt__event=event)
    return dict(
        groups=[groups[key] for key in sorted(groups)],
        inactive=historical.exclude(
            detection__in=eligible_face_detections(event, generations)
        ).count(),
    )


def inspect_schema() -> dict[str, Any]:
    """Candidate code can inspect a pre-expansion DB without selecting absent columns."""
    tables = set(connection.introspection.table_names())
    vector_table = FaceEmbeddingVector._meta.db_table
    legacy_table = FaceEmbedding._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        extension = cursor.fetchone()
        cursor.execute("SHOW server_version")
        postgres_version = cursor.fetchone()[0]
        cursor.execute(
            "SELECT datlocprovider, datcollversion, pg_database_collation_actual_version(oid) "
            "FROM pg_database WHERE datname = current_database()"
        )
        provider, recorded, actual = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM pg_collation WHERE collversion IS NOT NULL "
            "AND collversion IS DISTINCT FROM pg_collation_actual_version(oid)"
        )
        mismatched = cursor.fetchone()[0]
        columns = (
            {
                field.name
                for field in connection.introspection.get_table_description(cursor, vector_table)
            }
            if vector_table in tables
            else set()
        )
    required = {field.column for field in FaceEmbeddingVector._meta.local_fields}
    return dict(
        postgres_version=postgres_version,
        extension_version=extension[0] if extension else None,
        collation=dict(
            provider=provider,
            recorded_version=recorded,
            actual_version=actual,
            database_mismatch=recorded != actual,
            mismatched_count=mismatched,
        ),
        vector_table=vector_table in tables,
        missing_columns=sorted(required - columns),
        legacy_rows=FaceEmbedding.objects.count() if legacy_table in tables else None,
        vector_rows=FaceEmbeddingVector.objects.count() if vector_table in tables else None,
        ready=bool(extension and vector_table in tables and required <= columns),
    )


def vector_identity_gaps(
    event: Event,
    generations: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Same-statement scalar completeness proof for the parallel-store online transition.

    Value reconciliation remains an operator prerequisite; this query selects no biometric
    payload. Both stores attach independently to the same unique detection/accepted attempt.
    """
    missing, divergent = vector_identity_gap_predicates(generations)
    return eligible_face_detections(event, generations).aggregate(
        eligible=Count("pk"),
        missing=Count("pk", filter=missing),
        divergent=Count("pk", filter=divergent),
    )


def vector_identity_gap_predicates(
    generations: Sequence[Mapping[str, object]],
) -> tuple[Q, Q]:
    """Share scalar store completeness with the native reader's single snapshot."""
    unexpected_model = Q()
    vector_only = Q(pk__in=[])
    for generation in generations:
        identity = Q(
            attempt__contract_version=generation["contract_version"],
            attempt__processor_version=generation["processor_version"],
            attempt__job__configuration_hash=generation["configuration_hash"],
        )
        unexpected_model |= identity & ~Q(embedding_vector__model_version=generation["model"])
        if generation_uses_vector_only_storage(generation):
            vector_only |= identity
    return (
        Q(embedding_vector__isnull=True),
        Q(embedding_vector__isnull=False)
        & (
            (Q(embedding__isnull=True) & ~vector_only)
            | (
                Q(embedding__isnull=False)
                & ~Q(embedding_vector__model_version=F("embedding__model_version"))
            )
            | unexpected_model
        ),
    )


def inspect_inventory(event: Event | None = None) -> dict[str, Any]:
    """Bounded aggregate release inventory using only fields present before expansion."""
    from django.utils import timezone
    from feature_flags.models import FeatureFlag
    from selfie_search.models import SelfieSearch, SelfieSearchAttempt, SelfieSearchJob
    from selfie_search.services.jobs import MAX_ATTEMPTS as SELFIE_MAX_ATTEMPTS

    from processing.models import (
        EventFaceClusterActivation,
        EventProcessingRun,
        PhotoFaceEmbeddingProjection,
        ProcessingAttempt,
        ProcessingJob,
    )
    from processing.services.jobs import MAX_ATTEMPTS

    report = inspect_schema()
    tables = set(connection.introspection.table_names())
    now = timezone.now()

    def scope(model, event_path="event"):
        if model._meta.db_table not in tables:
            return None
        queryset = model.objects.all()
        return (
            queryset.filter(**{event_path: event}) if event is not None and event_path else queryset
        )

    def groups(queryset, fields):
        if queryset is None:
            return None
        rows = list(queryset.values(*fields).annotate(count=Count("pk")).order_by(*fields)[:1001])
        return dict(rows=rows[:1000], truncated=len(rows) > 1000)

    def leases(queryset):
        if queryset is None:
            return None
        return queryset.filter(status="in_progress").aggregate(
            active=Count("pk", filter=Q(lease_expires_at__gt=now)),
            expired=Count("pk", filter=Q(lease_expires_at__lte=now)),
            unknown=Count("pk", filter=Q(lease_expires_at__isnull=True)),
        )

    def retry_counts(queryset, limit):
        if queryset is None:
            return None
        return (
            queryset.filter(status="retry_wait")
            .annotate(attempt_count=Count("attempts"))
            .aggregate(
                ready=Count("pk", filter=Q(available_at__lte=now, attempt_count__lt=limit)),
                waiting=Count("pk", filter=Q(available_at__gt=now, attempt_count__lt=limit)),
                exhausted=Count("pk", filter=Q(attempt_count__gte=limit)),
            )
        )

    jobs = scope(ProcessingJob)
    attempts = scope(ProcessingAttempt)
    search_jobs = scope(SelfieSearchJob, "search__event")
    searches = scope(SelfieSearch)
    queued = searches.filter(status__in=("queued", "processing")) if searches is not None else None
    frozen = groups(
        queued,
        (
            "event_id",
            "status",
            "configuration_hash",
            "configuration__face_embedding__model",
            "configuration__gallery_face_embedding_generations",
        ),
    )
    if frozen is not None:
        for row in frozen["rows"]:
            raw = row.pop("configuration__gallery_face_embedding_generations")
            row["generations"] = [
                {
                    key: generation.get(key)
                    for key in (
                        "model",
                        "contract_version",
                        "processor_type",
                        "processor_version",
                        "configuration_hash",
                    )
                }
                for generation in (raw if isinstance(raw, list) else [])[:100]
                if isinstance(generation, dict)
            ]
    report.update(
        event=event.slug if event is not None else None,
        model_counts=groups(
            scope(FaceEmbedding, "detection__attempt__event"),
            ("detection__attempt__event_id", "model_version"),
        ),
        processing_contracts=groups(
            scope(EventProcessingRun),
            (
                "event_id",
                "contract_version",
                "processor_type",
                "processor_version",
                "configuration_hash",
                "status",
            ),
        ),
        jobs=groups(jobs, ("event_id", "processor_type", "status")),
        attempts=groups(attempts, ("event_id", "processor_type", "status", "accepted")),
        retries=retry_counts(jobs, MAX_ATTEMPTS),
        leases=leases(attempts),
        accepted_projections=groups(
            scope(PhotoFaceEmbeddingProjection, "photo__event"),
            (
                "photo__event_id",
                "contract_version",
                "processor_version",
                "configuration_hash",
            ),
        ),
        accepted_embeddings=groups(
            scope(FaceEmbedding, "detection__attempt__event").filter(
                detection__attempt__accepted=True
            )
            if FaceEmbedding._meta.db_table in tables
            else None,
            ("detection__attempt__event_id", "model_version"),
        ),
        queued_searches=frozen,
        search_jobs=groups(search_jobs, ("search__event_id", "status")),
        search_retries=retry_counts(search_jobs, SELFIE_MAX_ATTEMPTS),
        search_leases=leases(scope(SelfieSearchAttempt, "job__search__event")),
        cluster_activations=groups(
            scope(EventFaceClusterActivation),
            (
                "event_id",
                "active",
                "corpus__version",
                "corpus__model_version",
                "corpus__contract_version",
                "corpus__processor_version",
                "configuration_hash",
            ),
        ),
        flags=groups(scope(FeatureFlag, ""), ("key", "state")),
    )
    return report
