"""The explicit post-activation retirement preserves immutable audit/result identities."""

import json
from datetime import date
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from picflow.models import Event, Photo
from selfie_search.models import SelfieSearch, SelfieSearchDirectEvidence, SelfieSearchResult

from processing.models import (
    EventProcessingRun,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    ProcessingAttempt,
    ProcessingJob,
    ProcessingLateReceipt,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolate_published_test_events():
    Event.objects.published().update(publication_status="unavailable")


@pytest.fixture
def history():
    event = Event.objects.create(
        name="Retirement", slug="retirement", start_date=date.today(), end_date=date.today()
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE "
            "table_name='picflow_event' AND "
            "column_name='face_search_generation')"
        )
        if cursor.fetchone()[0]:
            cursor.execute("UPDATE picflow_event SET face_search_generation='adaface_v5'")
    photo = Photo.objects.create(id="retirement-photo", event=event, src="local.jpg")
    from processing.services.face_quality import active_face_embedding_generations

    generation = active_face_embedding_generations(event)[0]
    rows = []
    for index, (model, dimensions) in enumerate((("sface", 128), ("adaface-ir18-webface4m", 512))):
        fields = dict(
            event=event,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=3 if index == 0 else 5,
            configuration={"face_embedding": {"model": model}}
            if index == 0
            else generation["configuration"],
            configuration_hash=str(index) * 64 if index == 0 else generation["configuration_hash"],
        )
        run = EventProcessingRun.objects.create(**fields)
        job = ProcessingJob.objects.create(
            **fields, run=run, photo=photo, input_fingerprint={}, status="succeeded"
        )
        result: dict[str, Any] = {
            "model": model,
            "faces": [
                {
                    "embedding": [1.0] + [0.0] * (dimensions - 1),
                    "bbox": [1, 2, 3, 4],
                    "quality": 0.9,
                }
            ],
            "compute_ms": 42,
        }
        attempt = ProcessingAttempt.objects.create(
            **{k: v for k, v in fields.items() if k != "configuration_hash"},
            run=run,
            job=job,
            photo=photo,
            input_fingerprint={},
            status="succeeded",
            accepted=True,
            terminal_at=timezone.now(),
            result=result,
            result_hash="f" * 64,
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            attempt=attempt, artifact=artifact, face_index=0, status="kept"
        )
        # The state-only migration deliberately retains the pre-activation physical shape.
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO processing_faceembeddingvector (id, detection_id, "
                "model_version, vector, metadata, created_at) VALUES "
                "(gen_random_uuid(), %s, %s, %s::vector, '{}'::jsonb, now())",
                [detection.pk, model, str(result["faces"][0]["embedding"])],
            )
        projection = PhotoFaceEmbeddingProjection.objects.create(
            photo=photo,
            accepted_attempt=attempt,
            contract_version=3,
            processor_version=job.processor_version,
            configuration_hash=job.configuration_hash,
        )
        receipt = ProcessingLateReceipt.objects.create(
            attempt=attempt,
            received_at=timezone.now(),
            payload={"result": result, "status": "succeeded"},
            payload_hash="e" * 64,
        )
        rows.append((attempt, detection, projection, receipt))
    search = SelfieSearch.objects.create(event=event, status="ready", public_token_digest="d" * 64)
    result_row = SelfieSearchResult.objects.create(search=search, photo=photo, rank=1)
    SelfieSearchDirectEvidence.objects.create(
        result=result_row, detection=rows[0][1], cosine_distance=0.1
    )
    return rows


def run_retirement(**kwargs):
    output = StringIO()
    call_command("retire_legacy_face_vectors", stdout=output, **kwargs)
    return [json.loads(line) for line in output.getvalue().splitlines()]


def execute(**kwargs):
    return run_retirement(
        execute=True,
        confirm="RETIRE-LEGACY-FACE-VECTORS",
        active_build="a" * 40,
        backup_verified=True,
        old_processes_drained=True,
        current_cohorts_verified=True,
        **kwargs,
    )


def test_final_model_state_is_current_only():
    assert FaceEmbeddingVector._meta.get_field("vector").dimensions == 512
    constraint = FaceEmbeddingVector._meta.constraints[0]
    assert "sface" not in str(constraint.condition)


def test_dry_run_preserves_legacy_data_and_requires_execution_guards(history):
    receipt = run_retirement()[0]
    assert receipt["before"]["legacy_vectors"] == 1
    assert receipt["before"]["attempt_payloads"] == 1
    assert receipt["before"]["late_payloads"] == 1
    assert FaceEmbeddingVector.objects.count() == 2
    with pytest.raises(CommandError, match="requires"):
        run_retirement(execute=True)


def test_bounded_cleanup_preserves_current_vectors_hashes_and_saved_references(history):
    saved = list(SelfieSearchResult.objects.values())
    evidence = list(SelfieSearchDirectEvidence.objects.values())
    current = FaceEmbeddingVector.objects.get(model_version="adaface-ir18-webface4m")
    current_values = list(current.vector)
    receipt = execute(batch_size=1)[0]
    assert receipt["after"]["legacy_vectors"] == 0
    assert receipt["after"]["current_vectors"] == 1
    legacy, current_history = history
    legacy[0].refresh_from_db()
    legacy[3].refresh_from_db()
    assert legacy[0].result == {
        "model": "sface",
        "faces": [{"bbox": [1, 2, 3, 4], "quality": 0.9}],
        "compute_ms": 42,
    }
    assert legacy[0].result_hash == "f" * 64
    assert legacy[3].payload_hash == "e" * 64
    assert legacy[3].payload["result"] == legacy[0].result
    current_history[0].refresh_from_db()
    assert len(current_history[0].result["faces"][0]["embedding"]) == 512
    current.refresh_from_db()
    assert list(current.vector) == current_values
    assert PhotoFaceDetection.objects.count() == 2
    assert PhotoFaceEmbeddingProjection.objects.count() == 2
    assert list(SelfieSearchResult.objects.values()) == saved
    assert list(SelfieSearchDirectEvidence.objects.values()) == evidence
    assert execute(batch_size=1)[0]["removed"] == {
        "legacy_vectors": 0,
        "attempt_payloads": 0,
        "late_payloads": 0,
    }


def test_contraction_restores_immutability_and_rejects_legacy_vectors(history):
    execute(finalize=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE "
            "attrelid='processing_faceembeddingvector'::regclass AND "
            "attname='vector'"
        )
        assert cursor.fetchone()[0] == "vector(512)"
    for table in (
        "processing_faceembeddingvector",
        "processing_processingattempt",
        "processing_processinglatereceipt",
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table}")
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO processing_faceembeddingvector (id, detection_id, "
            "model_version, vector, metadata, created_at) VALUES "
            "(gen_random_uuid(), %s, 'sface', %s::vector, '{}'::jsonb, now())",
            [history[0][1].pk, str([1.0] + [0.0] * 511)],
        )
    assert execute(finalize=True)[0]["contracted"] is True


def test_failure_rolls_back_redaction_purge_and_trigger_changes(history):
    from processing.management.commands.retire_legacy_face_vectors import Command

    original_finalize = Command._finalize

    def fail_after_ddl(command, cursor):
        original_finalize(command, cursor)
        raise CommandError("forced finalization failure")

    with (
        patch.object(Command, "_finalize", fail_after_ddl),
        pytest.raises(CommandError, match="forced"),
    ):
        execute(finalize=True)
    assert FaceEmbeddingVector.objects.count() == 2
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE "
            "table_name='picflow_event' AND column_name='face_search_generation')"
        )
        assert cursor.fetchone()[0] is True
    history[0][0].refresh_from_db()
    assert "embedding" in history[0][0].result["faces"][0]
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM processing_faceembeddingvector")


def test_live_legacy_work_blocks_cleanup(history):
    fields = dict(
        event=history[0][0].event,
        contract_version=3,
        processor_type="face_embedding",
        processor_version=5,
        configuration={"face_embedding": {"model": "sface"}},
        configuration_hash="b" * 64,
    )
    run = EventProcessingRun.objects.create(**fields)
    ProcessingJob.objects.create(**fields, run=run, photo=history[0][0].photo, input_fingerprint={})
    with pytest.raises(CommandError, match="legacy work"):
        execute()
    assert FaceEmbeddingVector.objects.count() == 2


def test_batch_bound_is_restartable_and_finalization_refuses_remaining_material(history):
    legacy = history[0]
    extra = PhotoFaceDetection.objects.create(
        attempt=legacy[0], artifact=legacy[1].artifact, face_index=1, status="kept"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO processing_faceembeddingvector "
            "(id, detection_id, model_version, vector, metadata, created_at) "
            "VALUES (gen_random_uuid(), %s, 'sface', %s::vector, '{}'::jsonb, now())",
            [extra.pk, str([1.0] + [0.0] * 127)],
        )
    with pytest.raises(CommandError, match="zero legacy"):
        execute(batch_size=1, finalize=True)
    assert FaceEmbeddingVector.objects.filter(model_version="sface").count() == 2
    first = execute(batch_size=1)[0]
    assert first["removed"]["legacy_vectors"] == 1
    assert first["after"]["legacy_vectors"] == 1
    second = execute(batch_size=1, finalize=True)[0]
    assert second["before"]["legacy_vectors"] == 1
    assert second["after"]["legacy_vectors"] == 0
    assert second["contracted"] is True


def test_batched_payload_discovery_advances_past_committed_rows(history):
    from processing.management.commands.retire_legacy_face_vectors import Command

    original = history[0][0]
    another = ProcessingAttempt.objects.create(
        event=original.event,
        run=original.run,
        job=original.job,
        photo=original.photo,
        contract_version=original.contract_version,
        processor_type=original.processor_type,
        processor_version=original.processor_version,
        configuration=original.configuration,
        input_fingerprint=original.input_fingerprint,
        status="succeeded",
        terminal_at=timezone.now(),
        result=original.result,
        result_hash=original.result_hash,
    )
    seen_after_ids = []
    original_discover = Command._discover

    def discover(command, cursor, batch_size, counts, after_ids):
        seen_after_ids.append(after_ids.copy())
        return original_discover(command, cursor, batch_size, counts, after_ids)

    with patch.object(Command, "_discover", discover):
        receipts = execute(batch_size=1, max_batches=2)

    assert len(receipts) == 2
    assert seen_after_ids[0]["attempt_payloads"] is None
    assert seen_after_ids[1]["attempt_payloads"] is not None
    original.refresh_from_db()
    another.refresh_from_db()
    assert "embedding" not in original.result["faces"][0]
    assert "embedding" not in another.result["faces"][0]


def test_migration_cannot_purge_or_contract_before_activation():
    from importlib import import_module

    migration = import_module("processing.migrations.0018_retire_legacy_vector_state").Migration
    assert len(migration.operations) == 1
    assert migration.operations[0].database_operations == []


@pytest.mark.parametrize(
    "options", [{"batch_size": 0}, {"max_batches": 101}, {"timeout_seconds": 61}]
)
def test_command_rejects_unbounded_operations(options):
    with pytest.raises(CommandError, match="Bounds"):
        run_retirement(**options)


def test_inventory_and_discovery_do_not_run_under_mutation_locks(history):
    from processing.management.commands.retire_legacy_face_vectors import Command

    phases = []
    original_counts = Command._counts
    original_discover = Command._discover

    def counts(command, cursor):
        phases.append(command._holding_mutation_locks)
        return original_counts(command, cursor)

    def discover(command, cursor, batch_size, counts, after_ids):
        phases.append(command._holding_mutation_locks)
        return original_discover(command, cursor, batch_size, counts, after_ids)

    with patch.object(Command, "_counts", counts), patch.object(Command, "_discover", discover):
        receipt = execute()[0]
    assert phases and not any(phases)
    assert receipt["timing_ms"]["lock_hold"] >= 0
    assert receipt["timing_ms"]["discovery"] >= 0


def test_transaction_deadline_rolls_back_and_restores_guards(history):
    from processing.management.commands.retire_legacy_face_vectors import Command

    original_mutate = Command._mutate

    def expire_after_mutation(command, cursor, candidates):
        original_mutate(command, cursor, candidates)
        command._check_deadline(0.0)

    with patch.object(Command, "_mutate", expire_after_mutation):
        with pytest.raises(CommandError, match="deadline"):
            execute()
    assert FaceEmbeddingVector.objects.filter(model_version="sface").count() == 1
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM processing_faceembeddingvector")


def test_each_statement_receives_only_remaining_transaction_budget():
    from unittest.mock import Mock

    from processing.management.commands.retire_legacy_face_vectors import Command, DeadlineCursor

    raw_cursor = Mock()
    cursor = DeadlineCursor(raw_cursor, Command(), 12.0)
    clock_values = [10.0, 10.0, 10.1, 10.4, 10.4, 10.5]
    with patch(
        "processing.management.commands.retire_legacy_face_vectors.monotonic",
        side_effect=clock_values,
    ):
        cursor.execute("SELECT 1")
        cursor.execute("SELECT 2")
    assert raw_cursor.execute.call_args_list[0].args[1] == ["2000ms", "2000ms"]
    assert raw_cursor.execute.call_args_list[2].args[1] == ["1599ms", "1599ms"]


def publish_current_history(history):
    from processing.models import PhotoProcessingState

    attempt = history[1][0]
    event = attempt.event
    event.publication_status = Event.PublicationStatus.PUBLISHED
    event.save(update_fields=["publication_status"])
    PhotoProcessingState.objects.create(
        photo=attempt.photo,
        processor_type="face_embedding",
        status="succeeded",
        current_run_id=attempt.run_id,
        current_job_id=attempt.job_id,
        current_attempt=attempt,
        accepted_attempt=attempt,
    )
    return event


def test_published_current_cohort_reports_accepted_projection_gaps(history):
    baseline = Photo.objects.filter(event__publication_status="published").count()
    event = publish_current_history(history)
    Photo.objects.bulk_create(
        [Photo(id=f"accepted-gap-{index}", event=event, src="gap.jpg") for index in range(73)]
    )
    receipt = execute()[0]
    assert receipt["published_cohort"] == {
        "photos": baseline + 74,
        "without_current_projection": baseline + 73,
        "current_projections": 1,
        "events_without_current_projection": 0,
        "kept_detections": 1,
        "inconsistent_projections": 0,
        "invalid_native_vectors": 0,
    }


@pytest.mark.parametrize("failure", ["state", "hash", "vector"])
def test_inconsistent_published_current_cohort_blocks_before_mutation(history, failure):
    from processing.models import PhotoProcessingState

    publish_current_history(history)
    if failure == "state":
        PhotoProcessingState.objects.all().update(current_attempt=None)
    elif failure == "hash":
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE processing_photofaceembeddingprojection DISABLE TRIGGER "
                "proc_face_projection_identity_guard_trg"
            )
            cursor.execute(
                "ALTER TABLE processing_photofaceembeddingprojection DISABLE TRIGGER "
                "proc_face_projection_validate_trg"
            )
            PhotoFaceEmbeddingProjection.objects.filter(pk=history[1][2].pk).update(
                configuration_hash="z" * 64
            )
            cursor.execute(
                "ALTER TABLE processing_photofaceembeddingprojection ENABLE TRIGGER "
                "proc_face_projection_validate_trg"
            )
            cursor.execute(
                "ALTER TABLE processing_photofaceembeddingprojection ENABLE TRIGGER "
                "proc_face_projection_identity_guard_trg"
            )
    else:
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE processing_faceembeddingvector DISABLE TRIGGER "
                "proc_vector_evidence_trg"
            )
            cursor.execute(
                "DELETE FROM processing_faceembeddingvector WHERE "
                "model_version='adaface-ir18-webface4m'"
            )
            cursor.execute(
                "ALTER TABLE processing_faceembeddingvector ENABLE TRIGGER proc_vector_evidence_trg"
            )
    with pytest.raises(CommandError, match="published current cohort"):
        execute()
    assert FaceEmbeddingVector.objects.filter(model_version="sface").count() == 1


def test_finalize_drops_legacy_event_selector_without_updating_history(history):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE picflow_event SET face_search_generation='sface_v3'")
    execute(finalize=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE "
            "table_name='picflow_event' AND column_name='face_search_generation')"
        )
        assert cursor.fetchone()[0] is False
    assert execute(finalize=True)[0]["contracted"] is True


def test_finalize_requires_event_state_migration(history):
    from django.db.migrations.recorder import MigrationRecorder

    MigrationRecorder(connection).record_unapplied(
        "picflow", "0017_remove_event_face_search_generation_state"
    )
    with pytest.raises(CommandError, match="event.*migration"):
        execute(finalize=True)
    assert FaceEmbeddingVector.objects.count() == 2


def test_published_photos_without_any_current_projection_fail_closed():
    event = Event.objects.create(
        name="Unreconciled",
        slug="unreconciled",
        start_date=date.today(),
        end_date=date.today(),
        publication_status="published",
    )
    Photo.objects.create(id="unreconciled", event=event, src="old.jpg")
    with pytest.raises(CommandError, match="published current cohort"):
        execute()


def test_one_current_event_does_not_authorize_unreconciled_published_event(history):
    publish_current_history(history)
    event = Event.objects.create(
        name="Second unreconciled",
        slug="second-unreconciled",
        start_date=date.today(),
        end_date=date.today(),
        publication_status="published",
    )
    Photo.objects.create(id="second-unreconciled", event=event, src="old.jpg")
    with pytest.raises(CommandError, match="published current cohort"):
        execute()
    assert FaceEmbeddingVector.objects.filter(model_version="sface").count() == 1
    assert run_retirement()[0]["published_cohort"]["events_without_current_projection"] == 1
