"""Bounded release proof and post-activation physical schema contraction."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone
from picflow.models import Event

from processing.models import WorkerPool, WorkerPoolMember
from processing.services.vector_reconciliation import verify_scalar_embeddings

LEGACY_TABLE = "processing_faceembedding"
MAX_RETAINED_EVENTS = 1000


def _retained_events() -> list[Event]:
    events = list(Event.objects.only("pk").order_by("pk")[: MAX_RETAINED_EVENTS + 1])
    if len(events) > MAX_RETAINED_EVENTS:
        raise CommandError("Retained event limit exceeded; reviewed bounded release required")
    return events


def _set_timeout(timeout_seconds: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)", [str(timeout_seconds * 1000)]
        )
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", [str(timeout_seconds * 1000)])


def _event_verification(event: Event, timeout_seconds: int) -> dict[str, Any]:
    with transaction.atomic():
        _set_timeout(timeout_seconds)
        return verify_scalar_embeddings(event, include_hidden=True)


def verify_all_retained_events(*, timeout_seconds: int) -> dict[str, int]:
    """Cover all retained events; every biometric query is event scoped and timed."""
    if not 1 <= timeout_seconds <= 60:
        raise CommandError("timeout must be 1–60 seconds")
    with transaction.atomic():
        _set_timeout(timeout_seconds)
        events = _retained_events()
    eligible = 0
    for event in events:
        report = _event_verification(event, timeout_seconds)
        for group in report["groups"]:
            if any(group[field] for field in ("missing", "invalid", "divergent")):
                raise CommandError("Retained native cohort is incomplete or invalid")
            eligible += group["eligible"]
    return {"events": len(events), "eligible": eligible}


def retire_physical_schema(*, active_build: str, timeout_seconds: int) -> dict[str, Any]:
    """Only compatible active processes may cross the irreversible old-image boundary."""
    if "faceembedding" in apps.all_models["processing"]:
        raise CommandError("Active code still owns the JSON model")
    if len(active_build) != 40 or any(char not in "0123456789abcdef" for char in active_build):
        raise CommandError("Active candidate build must be an exact revision")
    with transaction.atomic():
        _set_timeout(timeout_seconds)
        required = {
            ("processing", "0017_retire_json_face_embedding"),
            ("selfie_search", "0007_remove_reader_review_context"),
        }
        if not required <= set(MigrationRecorder(connection).applied_migrations()):
            raise CommandError("Reviewed retirement state migrations are not applied")
        # Workers use the HTTP API; reject an old active cohort or live old session anyway.
        if (
            WorkerPool.objects.exclude(active_build=active_build).exists()
            or WorkerPool.objects.filter(staged_build__isnull=False).exists()
        ):
            raise CommandError("Worker pools have not committed the candidate build")
        if (
            WorkerPoolMember.objects.filter(
                heartbeat_at__gte=timezone.now() - timezone.timedelta(seconds=120)
            )
            .exclude(worker_build=active_build)
            .exists()
        ):
            raise CommandError("Old worker sessions have not drained")
        receipt = verify_all_retained_events(timeout_seconds=timeout_seconds)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", [LEGACY_TABLE])
            exists = cursor.fetchone()[0] is not None
            if exists:
                # Never CASCADE: incoming references or active old queries stop contraction.
                cursor.execute('LOCK TABLE "processing_faceembedding" IN ACCESS EXCLUSIVE MODE')
                cursor.execute(
                    "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
                    "AND confrelid = to_regclass(%s)",
                    [LEGACY_TABLE],
                )
                if cursor.fetchone()[0]:
                    raise CommandError("Legacy table has incoming foreign keys")
            if exists:
                cursor.execute('DROP TABLE "processing_faceembedding"')
            cursor.execute(
                'ALTER TABLE "selfie_search_selfiesearch" '
                'DROP COLUMN IF EXISTS "reader_staff_eligible", '
                'DROP COLUMN IF EXISTS "reader_comparison_requested"'
            )
    return {**receipt, "table_removed": True, "already_absent": not exists}
