"""Bounded, irreversible retirement after compatible web/worker activation.

Hashes remain receipts of the submitted payload, not checksums of the redacted JSON.
Each emitted batch receipt follows a committed transaction. Ordinary migrations do
not invoke this operation.
"""

import json
import math
from time import monotonic
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from picflow.models import Event

from processing.services.face_quality import active_face_embedding_generations

# Historical identities belong only to this one-time data transition.
LEGACY_MODEL = "sface"
CURRENT_MODEL = "adaface-ir18-webface4m"
CONFIRMATION = "RETIRE-LEGACY-FACE-VECTORS"
VECTOR_TABLE = "processing_faceembeddingvector"
PAYLOAD_TABLES = (
    ("processing_processingattempt", "result", "proc_terminal_attempt_guard_trg"),
    ("processing_processinglatereceipt", "payload", "proc_late_receipt_guard_trg"),
)
ARRAY_PATH = (
    'strict $.** ? (@.type() == "object" && exists(@.embedding)).embedding '
    '? (@.type() == "array" && @.size() == 128)'
)
LEGACY_PATH = (
    '$.** ? (@.model == "sface" || @.model_version == "sface" || @.embedding_model == "sface")'
)


def redact(value: Any, *, legacy: bool = False) -> Any:
    """Remove obsolete embedding arrays while retaining geometry and all audit fields."""
    if isinstance(value, dict):
        legacy = legacy or any(
            value.get(key) == LEGACY_MODEL for key in ("model", "model_version", "embedding_model")
        )
        return {
            key: redact(item, legacy=legacy)
            for key, item in value.items()
            if not (key == "embedding" and isinstance(item, list) and (legacy or len(item) == 128))
        }
    if isinstance(value, list):
        return [redact(item, legacy=legacy) for item in value]
    return value


class Command(BaseCommand):
    help = "Dry-run or bounded post-activation purge/redaction; --finalize fixes vector(512)."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--confirm")
        parser.add_argument("--active-build")
        parser.add_argument("--backup-verified", action="store_true")
        parser.add_argument("--old-processes-drained", action="store_true")
        parser.add_argument("--current-cohorts-verified", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--max-batches", type=int, default=1)
        parser.add_argument("--timeout-seconds", type=int, default=2)
        parser.add_argument("--finalize", action="store_true")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        max_batches = options["max_batches"]
        timeout = options["timeout_seconds"]
        if not 1 <= batch_size <= 1000 or not 1 <= max_batches <= 100 or not 1 <= timeout <= 60:
            raise CommandError(
                "Bounds require batch-size 1..1000, max-batches 1..100, timeout-seconds 1..60"
            )
        if options["execute"]:
            build = options["active_build"] or ""
            if (
                options["confirm"] != CONFIRMATION
                or len(build) != 40
                or any(char not in "0123456789abcdef" for char in build)
                or not all(
                    options[key]
                    for key in (
                        "backup_verified",
                        "old_processes_drained",
                        "current_cohorts_verified",
                    )
                )
            ):
                raise CommandError(
                    "Execution requires confirmation token, exact active build, "
                    "verified backup, old-process drain and verified current cohorts"
                )
            if ("processing", "0018_retire_legacy_vector_state") not in MigrationRecorder(
                connection
            ).applied_migrations():
                raise CommandError("Retirement state migration must be applied")
        if (
            options["execute"]
            and options["finalize"]
            and ("picflow", "0017_remove_event_face_search_generation_state")
            not in MigrationRecorder(connection).applied_migrations()
        ):
            raise CommandError(
                "Retirement event state migration must be applied before finalization"
            )
        self._holding_mutation_locks = False
        inventory_started = monotonic()
        with connection.cursor() as cursor:
            before = self._counts(cursor)
            if options["execute"]:
                self._require_drained(cursor)
        published_cohort = self._published_cohort()
        if options["execute"] and (
            published_cohort["inconsistent_projections"]
            or published_cohort["invalid_native_vectors"]
            or published_cohort["events_without_current_projection"]
        ):
            self.stdout.write(json.dumps({"published_cohort": published_cohort}, sort_keys=True))
            raise CommandError("Inconsistent published current cohort; retirement refused")
        inventory_ms = (monotonic() - inventory_started) * 1000
        after_ids: dict[str, Any] = {
            "legacy_vectors": None,
            "attempt_payloads": None,
            "late_payloads": None,
        }
        for batch in range(max_batches if options["execute"] else 1):
            discovery_started = monotonic()
            with connection.cursor() as cursor:
                candidates = (
                    self._discover(cursor, batch_size, before, after_ids)
                    if options["execute"]
                    else {}
                )
            discovery_ms = (monotonic() - discovery_started) * 1000
            planned = {
                key: len(candidates.get(key, []))
                for key in ("legacy_vectors", "attempt_payloads", "late_payloads")
            }
            if options["execute"] and any(before[key] and not planned[key] for key in planned):
                raise CommandError("Discovery exhausted before inventory reached zero")
            after = {key: value - planned.get(key, 0) for key, value in before.items()}
            remaining = any(after[key] for key in planned)
            if (
                options["execute"]
                and options["finalize"]
                and remaining
                and batch == max_batches - 1
            ):
                raise CommandError(
                    "Finalization requires zero legacy vectors and payloads; "
                    "rerun bounded cleanup first"
                )
            contracted = False
            lock_hold_ms = 0.0
            ddl_ms = 0.0
            if options["execute"]:
                started = monotonic()
                deadline = started + timeout
                try:
                    with transaction.atomic(), connection.cursor() as raw_cursor:
                        cursor = DeadlineCursor(raw_cursor, self, deadline)
                        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        tables = (
                            [VECTOR_TABLE, "picflow_event"]
                            if options["finalize"]
                            else [VECTOR_TABLE]
                            if candidates["legacy_vectors"]
                            else []
                        )
                        for (table, _, _), key in zip(
                            PAYLOAD_TABLES, ("attempt_payloads", "late_payloads"), strict=True
                        ):
                            if candidates[key]:
                                tables.append(table)
                        lock_started = monotonic()
                        if tables:
                            cursor.execute(
                                f"LOCK TABLE {', '.join(tables)} IN ACCESS EXCLUSIVE MODE"
                            )
                        self._holding_mutation_locks = True
                        self._mutate(cursor, candidates)
                        if options["finalize"] and not remaining:
                            ddl_started = monotonic()
                            self._finalize(cursor)
                            ddl_ms = (monotonic() - ddl_started) * 1000
                            contracted = True
                        self._check_deadline(deadline)
                    lock_hold_ms = (monotonic() - lock_started) * 1000 if tables else 0.0
                finally:
                    self._holding_mutation_locks = False
            receipt = {
                "mode": "execute" if options["execute"] else "dry_run",
                "batch": batch + 1,
                "before": before,
                "after": after,
                "removed": planned,
                "contracted": contracted,
                "published_cohort": published_cohort,
                "timing_ms": {
                    "inventory": round(inventory_ms, 3),
                    "discovery": round(discovery_ms, 3),
                    "lock_hold": round(lock_hold_ms, 3),
                    "final_ddl": round(ddl_ms, 3),
                },
                "transaction_budget_seconds": timeout,
                "aggregate_basis": "initial inventory minus committed selected mutations",
            }
            self.stdout.write(json.dumps(receipt, sort_keys=True))
            before = after
            if options["execute"]:
                for key, rows in candidates.items():
                    if rows:
                        after_ids[key] = rows[-1][0] if key != "legacy_vectors" else rows[-1]
            inventory_ms = 0.0
            if not remaining:
                break

    def _published_cohort(self) -> dict[str, int]:
        """Validate published v5 evidence before any exclusive lock, without vector payloads.

        Missing projections are accepted recognition loss. Existing v5 projections must
        agree with both approved generations and the exact succeeded current pointers.
        """
        approved = []
        parameters = []
        for generation in active_face_embedding_generations(Event()):
            approved.append(
                "(p.contract_version=%s AND p.processor_version=%s AND "
                "p.configuration_hash=%s AND a.configuration=%s::jsonb)"
            )
            parameters.extend(
                [
                    generation["contract_version"],
                    generation["processor_version"],
                    generation["configuration_hash"],
                    json.dumps(generation["configuration"]),
                ]
            )
        identity = " OR ".join(approved)
        valid = (
            f"({identity}) AND a.accepted AND a.status='succeeded' AND "
            "a.processor_type='face_embedding' AND a.photo_id=p.photo_id AND "
            "a.event_id=photo.event_id AND a.contract_version=p.contract_version AND "
            "a.processor_version=p.processor_version AND j.processor_type='face_embedding' AND "
            "j.photo_id=p.photo_id AND j.event_id=photo.event_id AND j.run_id=a.run_id AND "
            "j.contract_version=p.contract_version AND j.processor_version=p.processor_version AND "
            "j.configuration_hash=p.configuration_hash AND j.configuration=a.configuration AND "
            "r.processor_type='face_embedding' AND r.event_id=photo.event_id AND "
            "r.contract_version=p.contract_version AND r.processor_version=p.processor_version AND "
            "r.configuration_hash=p.configuration_hash AND r.configuration=a.configuration AND "
            "state.status='succeeded' AND state.accepted_attempt_id=a.id AND "
            "state.current_attempt_id=a.id AND state.current_job_id=j.id AND "
            "state.current_run_id=r.id"
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM picflow_photo photo JOIN picflow_event event "
                "ON event.id=photo.event_id WHERE event.publication_status='published'"
            )
            photos = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM (SELECT photo.event_id FROM picflow_photo photo "
                "JOIN picflow_event event ON event.id=photo.event_id "
                "LEFT JOIN processing_photofaceembeddingprojection p ON p.photo_id=photo.id "
                "LEFT JOIN processing_processingattempt a ON a.id=p.accepted_attempt_id "
                "WHERE event.publication_status='published' GROUP BY photo.event_id "
                f"HAVING count(p.id) FILTER (WHERE {identity})=0) unreconciled",
                parameters,
            )
            events_without_current_projection = cursor.fetchone()[0]
            # One aggregate join avoids a correlated anti-subquery for each projection.
            # No vector values or payloads leave the database.
            cursor.execute(
                "SELECT count(DISTINCT p.id), count(DISTINCT p.photo_id), "
                f"count(DISTINCT p.id) FILTER (WHERE NOT coalesce(({valid}), false)), "
                "count(d.id), count(d.id) FILTER (WHERE v.id IS NULL OR "
                "v.model_version<>%s OR vector_dims(v.vector)<>512) "
                "FROM processing_photofaceembeddingprojection p "
                "JOIN picflow_photo photo ON photo.id=p.photo_id "
                "JOIN picflow_event event ON event.id=photo.event_id "
                "LEFT JOIN processing_processingattempt a ON a.id=p.accepted_attempt_id "
                "LEFT JOIN processing_processingjob j ON j.id=a.job_id "
                "LEFT JOIN processing_eventprocessingrun r ON r.id=a.run_id "
                "LEFT JOIN processing_photoprocessingstate state ON state.photo_id=p.photo_id "
                "AND state.processor_type='face_embedding' "
                "LEFT JOIN processing_photofacedetection d ON d.attempt_id=a.id "
                "AND d.status='kept' "
                "LEFT JOIN processing_faceembeddingvector v ON v.detection_id=d.id "
                "WHERE event.publication_status='published' "
                "AND (p.processor_version=5 OR a.processor_version=5)",
                [*parameters, CURRENT_MODEL],
            )
            projections, projected_photos, inconsistent, kept, invalid_vectors = cursor.fetchone()
        return {
            "photos": photos,
            "without_current_projection": photos - projected_photos,
            "current_projections": projections,
            "events_without_current_projection": events_without_current_projection,
            "kept_detections": kept,
            "inconsistent_projections": inconsistent,
            "invalid_native_vectors": invalid_vectors,
        }

    def _check_deadline(self, deadline: float) -> None:
        if monotonic() >= deadline:
            raise CommandError("Mutation transaction deadline exceeded; batch rolled back")

    def _discover(
        self, cursor, batch_size: int, counts: dict[str, int], after_ids: dict[str, Any]
    ) -> dict[str, list]:
        # All scans, JSON parsing/redaction and bulk parameter construction happen
        # before any exclusive lock. Historical payloads are immutable; SQL below
        # rechecks their exact original value before changing them.
        vector_after = after_ids["legacy_vectors"]
        vector_cursor = " AND id > %s" if vector_after is not None else ""
        vector_parameters = [LEGACY_MODEL]
        if vector_after is not None:
            vector_parameters.append(vector_after)
        cursor.execute(
            f"SELECT id FROM {VECTOR_TABLE} WHERE model_version=%s"
            f"{vector_cursor} ORDER BY id LIMIT %s",
            [*vector_parameters, batch_size],
        )
        candidates: dict[str, list] = {"legacy_vectors": [row[0] for row in cursor.fetchall()]}
        for (table, field, _), key in zip(
            PAYLOAD_TABLES, ("attempt_payloads", "late_payloads"), strict=True
        ):
            candidates[key] = []
            if not counts[key]:
                continue
            configuration = "row.configuration" if field == "result" else "attempt.configuration"
            payload_after = after_ids[key]
            payload_cursor = " AND row.id > %s" if payload_after is not None else ""
            parameters = [LEGACY_PATH, ARRAY_PATH, LEGACY_PATH, LEGACY_PATH]
            if payload_after is not None:
                parameters.append(payload_after)
            cursor.execute(
                f"SELECT row.id, row.{field}, jsonb_path_exists({configuration}, "
                f"%s::jsonpath, '{{}}'::jsonb, true) FROM "
                f"{self._payload_source(table, field)} WHERE "
                f"{self._payload_predicate(table, field)}{payload_cursor} "
                f"ORDER BY row.id LIMIT %s",
                [*parameters, batch_size],
            )
            candidates[key] = []
            for row_id, payload, legacy in cursor.fetchall():
                original = json.loads(payload) if isinstance(payload, str) else payload
                candidates[key].append(
                    (row_id, json.dumps(original), json.dumps(redact(original, legacy=legacy)))
                )
        return candidates

    def _mutate(self, cursor, candidates: dict[str, list]) -> None:
        for (table, field, trigger), key in zip(
            PAYLOAD_TABLES, ("attempt_payloads", "late_payloads"), strict=True
        ):
            rows = candidates[key]
            if not rows:
                continue
            cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
            values = ", ".join(["(%s::uuid, %s::jsonb, %s::jsonb)"] * len(rows))
            parameters = [value for row in rows for value in row]
            cursor.execute(
                f"UPDATE {table} AS target SET {field}=selected.redacted "
                f"FROM (VALUES {values}) AS selected(id, original, redacted) "
                f"WHERE target.id=selected.id AND target.{field}=selected.original",
                parameters,
            )
            if cursor.rowcount != len(rows):
                raise CommandError("Selected historical payload changed; batch rolled back")
            cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
        vector_ids = candidates["legacy_vectors"]
        if vector_ids:
            cursor.execute(f"ALTER TABLE {VECTOR_TABLE} DISABLE TRIGGER proc_vector_evidence_trg")
            cursor.execute(
                f"DELETE FROM {VECTOR_TABLE} WHERE id=ANY(%s::uuid[]) AND model_version=%s",
                [vector_ids, LEGACY_MODEL],
            )
            if cursor.rowcount != len(vector_ids):
                raise CommandError("Selected legacy vector changed; batch rolled back")
            cursor.execute(f"ALTER TABLE {VECTOR_TABLE} ENABLE TRIGGER proc_vector_evidence_trg")

    def _payload_predicate(self, table: str, field: str) -> str:
        # Pre-contract results can omit model; an actual 128D array still identifies
        # obsolete material. Configuration identifies model-labelled larger arrays.
        configuration = "row.configuration" if field == "result" else "attempt.configuration"
        return (
            f"(jsonb_path_exists(row.{field}, %s::jsonpath, '{{}}'::jsonb, true) OR "
            f"((jsonb_path_exists({configuration}, %s::jsonpath, "
            f"'{{}}'::jsonb, true) OR jsonb_path_exists(row.{field}, "
            f"%s::jsonpath, '{{}}'::jsonb, true)) AND "
            f"jsonb_path_exists(row.{field}, 'strict $.**.embedding ? "
            f"(@.type() == \"array\")', '{{}}'::jsonb, true)))"
        )

    def _payload_source(self, table: str, field: str) -> str:
        if field == "payload":
            return (
                f"{table} row JOIN processing_processingattempt attempt ON "
                "attempt.id=row.attempt_id"
            )
        return f"{table} row"

    def _counts(self, cursor) -> dict[str, int]:
        cursor.execute(
            f"SELECT count(*) FILTER (WHERE model_version=%s), count(*) FILTER "
            f"(WHERE model_version=%s) FROM {VECTOR_TABLE}",
            [LEGACY_MODEL, CURRENT_MODEL],
        )
        legacy, current = cursor.fetchone()
        counts = {"legacy_vectors": legacy, "current_vectors": current}
        for (table, field, _), key in zip(
            PAYLOAD_TABLES, ("attempt_payloads", "late_payloads"), strict=True
        ):
            cursor.execute(
                f"SELECT count(*) FROM {self._payload_source(table, field)} WHERE "
                f"{self._payload_predicate(table, field)}",
                [ARRAY_PATH, LEGACY_PATH, LEGACY_PATH],
            )
            counts[key] = cursor.fetchone()[0]
        return counts

    def _require_drained(self, cursor) -> None:
        for table, clause in (
            ("processing_processingjob", "status IN ('queued', 'processing', 'retry_wait')"),
            ("processing_processingattempt", "status='in_progress'"),
            (
                "selfie_search_selfiesearch",
                "status NOT IN ('ready', 'no_face', 'multiple_faces', 'quality_rejected', "
                "'search_unavailable', 'failed')",
            ),
            ("selfie_search_selfiesearchjob", "status IN ('queued', 'processing', 'retry_wait')"),
        ):
            cursor.execute(
                f"SELECT count(*) FROM {table} WHERE {clause} AND "
                f"jsonb_path_exists(configuration, %s::jsonpath, '{{}}'::jsonb, "
                f"true)",
                [LEGACY_PATH],
            )
            if cursor.fetchone()[0]:
                raise CommandError("Nonterminal legacy work must be drained before retirement")
        cursor.execute(
            "SELECT count(*) FROM processing_eventfaceclusteractivation "
            "activation JOIN processing_faceclustercorpus corpus ON "
            "corpus.id=activation.corpus_id WHERE activation.active AND "
            "corpus.model_version=%s",
            [LEGACY_MODEL],
        )
        if cursor.fetchone()[0]:
            raise CommandError("Active legacy cluster corpus must be deactivated")

    def _finalize(self, cursor) -> None:
        cursor.execute("ALTER TABLE picflow_event DROP COLUMN IF EXISTS face_search_generation")
        cursor.execute(f"ALTER TABLE {VECTOR_TABLE} DROP CONSTRAINT proc_vector_model_dimension")
        cursor.execute(
            f"ALTER TABLE {VECTOR_TABLE} ALTER COLUMN vector TYPE vector(512) "
            f"USING vector::vector(512)"
        )
        cursor.execute(
            f"ALTER TABLE {VECTOR_TABLE} ADD CONSTRAINT "
            f"proc_vector_model_dimension CHECK (model_version = "
            f"'adaface-ir18-webface4m' AND vector_dims(vector) = 512)"
        )


class DeadlineCursor:
    """Each statement consumes the remaining transaction budget, never a fresh one."""

    def __init__(self, cursor, command: Command, deadline: float):
        self.cursor = cursor
        self.command = command
        self.deadline = deadline

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, sql, params=None):
        self.command._check_deadline(self.deadline)
        milliseconds = max(1, math.floor((self.deadline - monotonic()) * 1000))
        self.cursor.execute(
            "SELECT set_config('statement_timeout', %s, true), "
            "set_config('lock_timeout', %s, true)",
            [f"{milliseconds}ms", f"{milliseconds}ms"],
        )
        self.cursor.execute(sql, params)
        self.command._check_deadline(self.deadline)
