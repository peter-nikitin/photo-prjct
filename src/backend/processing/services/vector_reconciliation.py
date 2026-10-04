"""Bounded private operator reconciliation of immutable historical face evidence."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from django.db import connection
from django.db.models import Count, F, FloatField, Func, IntegerField, Q
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from picflow.models import Event

from processing.models import FaceEmbeddingVector
from processing.services.face_cohort import eligible_face_detections
from processing.services.vector_embeddings import (
    validate_embedding,
    vector_values,
)


def iter_reconciliation_rows(
    event: Event | None = None,
    generations: Sequence[Mapping[str, object]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream private native evidence and its immutable generation identity."""
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
        model = str(
            expected_models.get(
                (
                    row["attempt__contract_version"],
                    row["attempt__processor_version"],
                    row["attempt__job__configuration_hash"],
                )
            )
            or frozen_model
            or row["embedding_vector__model_version"]
            or "unknown"
        )
        key = str(row["attempt__event_id"]), model
        group = groups.setdefault(
            key, dict(event=key[0], model=model, eligible=0, missing=0, divergent=0, invalid=0)
        )
        group["eligible"] += 1
        if row["embedding_vector__id"] is None:
            group["missing"] += 1
            continue
        if (
            row["embedding_vector__model_version"] != model
            or row["attempt__configuration"] != configuration
            or row["attempt__run__configuration"] != configuration
        ):
            group["invalid"] += 1
            continue
        try:
            validate_embedding(vector_values(row["embedding_vector__vector"]), model_version=model)
        except (ValueError, TypeError):
            group["invalid"] += 1
    historical = FaceEmbeddingVector.objects.all()
    if event is not None:
        historical = historical.filter(detection__attempt__event=event)
    return dict(
        groups=[groups[key] for key in sorted(groups)],
        inactive=historical.exclude(
            detection__in=eligible_face_detections(event, generations)
        ).count(),
    )


def verify_scalar_embeddings(event: Event, *, include_hidden: bool = False) -> dict[str, Any]:
    """Event-bounded SQL validity proof without transferring biometric payloads."""
    rows = eligible_face_detections(event, include_hidden=include_hidden).annotate(
        frozen_model=KeyTextTransform(
            "model", KeyTransform("face_embedding", "attempt__job__configuration")
        ),
        dimensions=Func(
            "embedding_vector__vector", function="vector_dims", output_field=IntegerField()
        ),
        norm=Func("embedding_vector__vector", function="vector_norm", output_field=FloatField()),
    )
    wrong_model = Q(frozen_model__isnull=False) & ~Q(
        embedding_vector__model_version=F("frozen_model")
    )
    invalid = (
        wrong_model
        | (Q(embedding_vector__model_version="adaface-ir18-webface4m") & ~Q(dimensions=512))
        | ~Q(embedding_vector__model_version="adaface-ir18-webface4m")
        | Q(norm__lt=0.999999)
        | Q(norm__gt=1.000001)
        | ~Q(attempt__configuration=F("attempt__job__configuration"))
        | ~Q(attempt__run__configuration=F("attempt__job__configuration"))
    ) & Q(embedding_vector__isnull=False)
    grouped = list(
        rows.values("frozen_model", "embedding_vector__model_version")
        .annotate(
            eligible=Count("pk"),
            missing=Count("pk", filter=Q(embedding_vector__isnull=True)),
            invalid=Count("pk", filter=invalid),
        )
        .order_by("frozen_model", "embedding_vector__model_version")[:1001]
    )
    if len(grouped) > 1000:
        raise ValueError("native verification group limit exceeded")
    return dict(
        groups=[
            dict(
                event=str(event.pk),
                model=row["frozen_model"] or row["embedding_vector__model_version"] or "unknown",
                eligible=row["eligible"],
                missing=row["missing"],
                invalid=row["invalid"],
                divergent=0,
            )
            for row in grouped
        ]
    )


def inspect_schema() -> dict[str, Any]:
    """Candidate code can inspect a pre-expansion DB without selecting absent columns."""
    tables = set(connection.introspection.table_names())
    vector_table = FaceEmbeddingVector._meta.db_table
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
        vector_rows=FaceEmbeddingVector.objects.count() if vector_table in tables else None,
        ready=bool(extension and vector_table in tables and required <= columns),
    )


def vector_identity_gaps(
    event: Event,
    generations: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Same-statement scalar completeness proof without selecting biometric payloads."""
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
    for generation in generations:
        identity = Q(
            attempt__contract_version=generation["contract_version"],
            attempt__processor_version=generation["processor_version"],
            attempt__job__configuration_hash=generation["configuration_hash"],
        )
        unexpected_model |= identity & ~Q(embedding_vector__model_version=generation["model"])
    return Q(embedding_vector__isnull=True), Q(embedding_vector__isnull=False) & unexpected_model


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
            scope(FaceEmbeddingVector, "detection__attempt__event"),
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
            scope(FaceEmbeddingVector, "detection__attempt__event").filter(
                detection__attempt__accepted=True
            )
            if FaceEmbeddingVector._meta.db_table in tables
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
