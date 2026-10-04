import pytest
from django.apps import apps
from django.db import migrations

from processing.tests.test_vector_embeddings import detection  # noqa: F401
from processing.tests.test_vector_reconciliation import native_detection  # noqa: F401


def test_legacy_model_retirement_is_state_only():
    from importlib import import_module

    migration = import_module("processing.migrations.0017_retire_json_face_embedding").Migration
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, migrations.SeparateDatabaseAndState)
    assert operation.database_operations == []
    assert isinstance(operation.state_operations[0], migrations.DeleteModel)
    assert operation.state_operations[0].name == "FaceEmbedding"
    assert "faceembedding" not in apps.all_models["processing"]


def test_retirement_requires_explicit_review_and_drain(monkeypatch):
    import pytest
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="reviewed release"):
        call_command("retire_json_face_embeddings", execute=True)


@pytest.mark.django_db
def test_all_event_gate_stops_on_any_gap(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from django.core.management.base import CommandError

    from processing.services import legacy_embedding_retirement as retirement

    events = [SimpleNamespace(pk=1), SimpleNamespace(pk=2)]
    monkeypatch.setattr(retirement, "_retained_events", lambda: events)
    seen = []

    def verify(event):
        seen.append(event.pk)
        return {
            "groups": [{"eligible": 1, "missing": int(event.pk == 2), "invalid": 0, "divergent": 0}]
        }

    monkeypatch.setattr(retirement, "verify_scalar_embeddings", verify)
    monkeypatch.setattr(retirement, "_event_verification", lambda event, timeout: verify(event))
    with pytest.raises(CommandError, match="incomplete"):
        retirement.verify_all_retained_events(timeout_seconds=15)
    assert seen == [1, 2]


@pytest.mark.django_db
def test_flush_includes_retained_physical_table():
    from django.core.management.color import no_style
    from django.db import connection

    sql = connection.ops.sql_flush(no_style(), ["processing_photofacedetection"])
    assert '"processing_faceembedding"' in sql[0]


@pytest.mark.django_db
def test_drop_is_idempotent_and_preserves_native_schema(monkeypatch):
    from django.core.management import call_command
    from django.db import connection

    from processing.services import legacy_embedding_retirement as retirement

    monkeypatch.setattr(
        retirement, "verify_all_retained_events", lambda **kwargs: {"events": 0, "eligible": 0}
    )
    options = dict(
        execute=True, reviewed_release=True, old_processes_drained=True, active_build="a" * 40
    )
    call_command("retire_json_face_embeddings", **options)
    call_command("retire_json_face_embeddings", **options)
    tables = connection.introspection.table_names()
    assert "processing_faceembedding" not in tables
    assert "processing_faceembeddingvector" in tables
    assert "selfie_search_selfiesearchresult" in tables


@pytest.mark.django_db
def test_incoming_foreign_key_stops_drop(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import connection

    from processing.services import legacy_embedding_retirement as retirement

    monkeypatch.setattr(
        retirement, "verify_all_retained_events", lambda **kwargs: {"events": 0, "eligible": 0}
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE retirement_incoming (id uuid REFERENCES processing_faceembedding(id))"
        )
    with pytest.raises(CommandError, match="incoming foreign keys"):
        call_command(
            "retire_json_face_embeddings",
            execute=True,
            reviewed_release=True,
            old_processes_drained=True,
            active_build="a" * 40,
        )
    assert "processing_faceembedding" in connection.introspection.table_names()


@pytest.mark.django_db
@pytest.mark.parametrize("staged_build", [None, "d" * 40])
def test_distinct_http_worker_builds_do_not_block_physical_drop(monkeypatch, staged_build):
    from uuid import uuid4

    from django.core.management import call_command
    from django.db import connection
    from django.utils import timezone

    from processing.models import WorkerPool, WorkerPoolMember
    from processing.services import legacy_embedding_retirement as retirement

    monkeypatch.setattr(
        retirement, "verify_all_retained_events", lambda **kwargs: {"events": 0, "eligible": 0}
    )
    pool = WorkerPool.objects.create(
        name="bulk", group_id="bulk-group", active_build="b" * 40, staged_build=staged_build
    )
    WorkerPoolMember.objects.create(
        pool=pool,
        instance_id="ready-worker",
        boot_id=uuid4(),
        worker_build="c" * 40,
        ready=True,
        heartbeat_at=timezone.now(),
    )
    call_command(
        "retire_json_face_embeddings",
        execute=True,
        reviewed_release=True,
        old_processes_drained=True,
        active_build="a" * 40,
    )
    assert "processing_faceembedding" not in connection.introspection.table_names()


def test_reader_columns_are_retained_before_activation():
    from importlib import import_module

    migration = import_module(
        "selfie_search.migrations.0007_remove_reader_review_context"
    ).Migration
    operation = migration.operations[0]
    assert isinstance(operation, migrations.SeparateDatabaseAndState)
    assert all(isinstance(op, migrations.RemoveField) for op in operation.state_operations)
    sql = operation.database_operations[0].sql
    assert "SET DEFAULT false" in sql
    assert "DROP COLUMN" not in sql


@pytest.mark.django_db
@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
@pytest.mark.parametrize("native_model", [None, "sface"])
def test_hidden_retained_face_blocks_release_when_native_evidence_is_missing_or_invalid(
    native_detection,  # noqa: F811 - imported shared pytest fixture
    native_model,
):
    from django.core.management.base import CommandError

    from processing.models import FaceEmbeddingVector
    from processing.services.face_cohort import eligible_face_detections
    from processing.services.legacy_embedding_retirement import verify_all_retained_events

    attempt = native_detection.attempt
    photo = attempt.photo
    photo.is_hidden = True
    photo.save(update_fields=["is_hidden"])
    if native_model:
        FaceEmbeddingVector.objects.create(
            detection=native_detection, model_version=native_model, vector=[1.0] + [0.0] * 127
        )
    assert not eligible_face_detections(attempt.event).exists()
    with pytest.raises(CommandError, match="incomplete or invalid"):
        verify_all_retained_events(timeout_seconds=15)


@pytest.mark.django_db
@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
def test_valid_hidden_native_face_is_verified_and_can_be_shown_without_reprocessing(
    native_detection,  # noqa: F811 - imported shared pytest fixture
):
    from picflow.event_management import EventPhotoSelection, apply_event_photo_action
    from picflow.models import Event

    from processing.models import FaceEmbeddingVector
    from processing.services.face_cohort import eligible_face_detections
    from processing.services.legacy_embedding_retirement import verify_all_retained_events

    attempt = native_detection.attempt
    photo = attempt.photo
    photo.is_hidden = True
    photo.save(update_fields=["is_hidden"])
    FaceEmbeddingVector.objects.create(
        detection=native_detection,
        model_version="adaface-ir18-webface4m",
        vector=[1.0] + [0.0] * 511,
    )
    assert verify_all_retained_events(timeout_seconds=15) == {
        "events": Event.objects.count(),
        "eligible": 1,
    }
    assert not eligible_face_detections(attempt.event).exists()
    assert (
        apply_event_photo_action(
            attempt.event, EventPhotoSelection.explicit((photo.pk,)), "show", None
        )
        == 1
    )
    assert eligible_face_detections(attempt.event).get() == native_detection
