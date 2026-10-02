import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from processing.models import FaceEmbedding, FaceEmbeddingVector, PhotoFaceEmbeddingProjection
from processing.services.face_quality import historical_adaface_face_embedding_generations
from processing.services.vector_reconciliation import backfill_embeddings, verify_embeddings
from processing.tests.test_vector_embeddings import detection  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def historical(request):
    source_detection = request.getfixturevalue("detection")
    photo = source_detection.attempt.photo
    photo.src = ""
    photo.original_key = "private/original.jpg"
    photo.original_size = 1
    photo.original_filename = "original.jpg"
    photo.original_content_type = "image/jpeg"
    photo.uploaded_by = get_user_model().objects.create_user(username="historical-owner")
    photo.uploaded_at = timezone.now()
    photo.save()
    PhotoFaceEmbeddingProjection.objects.create(
        photo=photo,
        accepted_attempt=source_detection.attempt,
        contract_version=1,
        processor_version=1,
        configuration_hash="a" * 64,
    )
    return FaceEmbedding.objects.create(
        detection=source_detection,
        model_version=getattr(request, "param", {}).get("model", "sface"),
        vector=getattr(request, "param", {}).get("vector", [1.0] + [0.0] * 127),
        metadata={"embedding": [1.0], "quality": 0.9},
    )


@pytest.fixture
def native_detection(detection):  # noqa: F811 - imported pytest fixture dependency
    source_detection = detection
    photo = source_detection.attempt.photo
    photo.src = ""
    photo.original_key = "private/original.jpg"
    photo.original_size = 1
    photo.original_filename = "original.jpg"
    photo.original_content_type = "image/jpeg"
    photo.uploaded_by = get_user_model().objects.create_user(username="native-owner")
    photo.uploaded_at = timezone.now()
    photo.save()
    PhotoFaceEmbeddingProjection.objects.create(
        photo=photo,
        accepted_attempt=source_detection.attempt,
        contract_version=source_detection.attempt.contract_version,
        processor_version=source_detection.attempt.processor_version,
        configuration_hash=source_detection.attempt.job.configuration_hash,
    )
    return source_detection


def test_unmarked_native_vector_without_json_remains_invalid(native_detection):
    FaceEmbeddingVector.objects.create(
        detection=native_detection,
        model_version="sface",
        vector=[1.0] + [0.0] * 127,
    )
    assert verify_embeddings()["groups"][0]["invalid"] == 1


@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
@pytest.mark.parametrize("explicit_generation", [False, True])
def test_native_only_reconciliation_admits_exact_marked_generation(
    native_detection, explicit_generation
):
    FaceEmbeddingVector.objects.create(
        detection=native_detection,
        model_version="adaface-ir18-webface4m",
        vector=[1.0] + [0.0] * 511,
        metadata={"quality": 0.9},
    )
    generations = historical_adaface_face_embedding_generations() if explicit_generation else None
    report = verify_embeddings(native_detection.attempt.event, generations)
    assert report["groups"] == [
        {
            "event": str(native_detection.attempt.event_id),
            "model": "adaface-ir18-webface4m",
            "eligible": 1,
            "missing": 0,
            "invalid": 0,
            "divergent": 0,
        }
    ]
    assert not FaceEmbedding.objects.exists()
    call_command("verify_pgvector_face_embeddings", stdout=StringIO())


@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
@pytest.mark.parametrize("native_model", [None, "sface"])
def test_native_reconciliation_rejects_missing_or_wrong_model(native_detection, native_model):
    if native_model:
        FaceEmbeddingVector.objects.create(
            detection=native_detection,
            model_version=native_model,
            vector=[1.0] + [0.0] * 127,
        )
    report = verify_embeddings(
        native_detection.attempt.event, historical_adaface_face_embedding_generations()
    )
    group = report["groups"][0]
    assert group["missing"] + group["invalid"] + group["divergent"] == 1
    with pytest.raises(CommandError):
        call_command("verify_pgvector_face_embeddings", stdout=StringIO())


def test_dry_run_bounds_resume_idempotence_and_sanitizer(historical):
    before = historical.detection.attempt.__dict__.copy()
    dry = backfill_embeddings(batch_size=1, max_rows=1)
    assert dry["missing"] == 1
    assert dry["cursor"] == str(historical.pk)
    assert not FaceEmbeddingVector.objects.exists()
    applied = backfill_embeddings(apply=True, batch_size=1, max_rows=1)
    assert applied["created"] == 1
    assert FaceEmbeddingVector.objects.get().metadata == {"quality": 0.9}
    assert backfill_embeddings(apply=True, batch_size=1, max_rows=1)["existing"] == 1
    assert backfill_embeddings(after=historical.pk, batch_size=1, max_rows=1)["scanned"] == 0
    historical.detection.attempt.refresh_from_db()
    assert historical.detection.attempt.status == before["status"]
    assert historical.detection.attempt.accepted == before["accepted"]
    assert verify_embeddings()["groups"][0]["missing"] == 0


def test_divergence_never_overwrites(historical):
    row = FaceEmbeddingVector.objects.create(
        detection=historical.detection,
        model_version="sface",
        vector=[0.0, 1.0] + [0.0] * 126,
    )
    report = backfill_embeddings(apply=True, batch_size=1, max_rows=1)
    assert report["divergent"] == 1
    row.refresh_from_db()
    assert row.vector[1] == 1.0
    assert verify_embeddings()["groups"][0]["divergent"] == 1


def test_invalid_source_and_hidden_inventory(historical):
    # Legacy evidence can predate the new validation contract.
    with patch(
        "processing.services.vector_reconciliation.validate_embedding", side_effect=ValueError
    ):
        assert backfill_embeddings(apply=True, batch_size=1, max_rows=1)["invalid"] == 1
        assert verify_embeddings()["groups"][0]["invalid"] == 1
    historical.detection.attempt.photo.is_hidden = True
    historical.detection.attempt.photo.save()
    report = verify_embeddings()
    assert report["groups"] == []
    assert report["inactive"] == 1


def test_commands_explicit_apply_and_failure(historical):
    out = StringIO()
    call_command("backfill_pgvector_face_embeddings", batch_size=1, max_rows=1, stdout=out)
    assert not FaceEmbeddingVector.objects.exists()
    with pytest.raises(CommandError):
        call_command("verify_pgvector_face_embeddings", stdout=StringIO())
    call_command(
        "backfill_pgvector_face_embeddings",
        apply=True,
        batch_size=1,
        max_rows=1,
        stdout=StringIO(),
    )
    call_command("verify_pgvector_face_embeddings", stdout=StringIO())
    assert "private/original" not in out.getvalue()
    assert "embedding" not in out.getvalue()


def test_inventory_before_schema_expansion():
    with patch(
        "processing.services.vector_reconciliation.connection.introspection.table_names",
        return_value=[],
    ):
        out = StringIO()
        call_command("inspect_pgvector_face_search", stdout=out)
    assert '"vector_table": false' in out.getvalue()


def test_backfill_checks_concurrent_insert(historical):
    manager = FaceEmbeddingVector.objects
    original = manager.get_or_create

    def race(**kwargs):
        manager.create(
            detection=historical.detection,
            model_version="sface",
            vector=[1.0] + [0.0] * 127,
            metadata={"quality": 0.9},
        )
        return original(**kwargs)

    with patch.object(manager, "get_or_create", side_effect=race):
        assert backfill_embeddings(apply=True, batch_size=1, max_rows=1)["existing"] == 1
    assert manager.count() == 1


@pytest.mark.parametrize(
    "historical",
    [
        {"vector": [True] + [0.0] * 127},
        {"vector": [1.0]},
        {"vector": [0.0] * 128},
        {"model": "unknown"},
    ],
    indirect=True,
)
def test_invalid_eligible_json_blocks_acceptance(historical):
    assert backfill_embeddings(apply=True)["invalid"] == 1
    assert not FaceEmbeddingVector.objects.exists()
    assert verify_embeddings()["groups"][0]["invalid"] == 1


@pytest.mark.parametrize(
    "historical",
    [
        {"model": "sface", "vector": [2**-0.5, 2**-0.5] + [0.0] * 126},
        {"model": "adaface-ir18-webface4m", "vector": [2**-0.5, 2**-0.5] + [0.0] * 510},
    ],
    indirect=True,
)
def test_both_models_use_exact_float32_conversion(historical):
    assert backfill_embeddings(apply=True)["created"] == 1
    assert backfill_embeddings(apply=True)["existing"] == 1
    assert verify_embeddings()["groups"][0]["divergent"] == 0


def test_bounds_stop_and_resume(historical):
    from processing.models import PhotoFaceDetection

    second_detection = PhotoFaceDetection.objects.create(
        attempt=historical.detection.attempt,
        artifact=historical.detection.artifact,
        face_index=1,
        status="kept",
    )
    FaceEmbedding.objects.create(
        detection=second_detection,
        model_version="sface",
        vector=[1.0] + [0.0] * 127,
    )
    first = backfill_embeddings(apply=True, batch_size=1, max_rows=1)
    assert first["scanned"] == first["created"] == 1
    assert first["has_more"]
    from uuid import UUID

    second = backfill_embeddings(apply=True, batch_size=1, max_rows=1, after=UUID(first["cursor"]))
    assert second["created"] == 1
    assert not second["has_more"]
    assert FaceEmbeddingVector.objects.count() == 2


def test_explicit_generation_and_foreign_event(historical):
    from processing.services.face_cohort import eligible_face_detections

    generation = dict(
        model="sface",
        contract_version=1,
        processor_type="face_embedding",
        processor_version=1,
        configuration={},
        configuration_hash="a" * 64,
    )
    assert eligible_face_detections(historical.detection.attempt.event, [generation]).count() == 1
    assert not eligible_face_detections(
        generations=[dict(generation, configuration_hash="b" * 64)]
    ).exists()
    # Projection pointing at evidence from another photo/event cannot enter the cohort.
    from datetime import date

    from picflow.models import Event

    event = Event.objects.create(
        name="Foreign", slug="foreign", start_date=date.today(), end_date=date.today()
    )
    assert verify_embeddings(event)["groups"] == []


def test_inventory_reports_ready_schema_and_collation(historical):
    out = StringIO()
    call_command("inspect_pgvector_face_search", stdout=out)
    report = json.loads(out.getvalue())
    assert report["ready"]
    assert report["legacy_rows"] == 1
    assert report["collation"]["mismatched_count"] == 0
    assert report["collation"]["database_mismatch"] is False


def test_stale_historical_projection_is_inactive(historical):
    from processing.models import (
        FaceProcessingAttemptArtifact,
        PhotoFaceDetection,
        ProcessingAttempt,
    )

    original = historical.detection.attempt
    replacement = ProcessingAttempt.objects.create(
        event=original.event,
        run=original.run,
        job=original.job,
        photo=original.photo,
        contract_version=1,
        processor_type="face_embedding",
        processor_version=1,
        status="succeeded",
        accepted=True,
        terminal_at=timezone.now(),
    )
    artifact = FaceProcessingAttemptArtifact.objects.create(attempt=replacement)
    kept = PhotoFaceDetection.objects.create(
        attempt=replacement,
        artifact=artifact,
        face_index=0,
        status="kept",
    )
    FaceEmbedding.objects.create(detection=kept, model_version="sface", vector=[1.0] + [0.0] * 127)
    PhotoFaceEmbeddingProjection.objects.update(accepted_attempt=replacement)
    report = verify_embeddings()
    assert report["inactive"] == 1
    assert report["groups"][0]["eligible"] == 1
    assert backfill_embeddings(apply=True)["created"] == 2
    assert verify_embeddings()["groups"][0]["missing"] == 0


def test_scalar_identity_gaps_never_select_vectors(historical):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from processing.services.vector_reconciliation import vector_identity_gaps

    generation = dict(
        model="sface",
        contract_version=1,
        processor_type="face_embedding",
        processor_version=1,
        configuration={},
        configuration_hash="a" * 64,
    )
    with CaptureQueriesContext(connection) as queries:
        report = vector_identity_gaps(historical.detection.attempt.event, [generation])
    assert report == dict(eligible=1, missing=1, divergent=0)
    assert len(queries) == 1
    assert '."vector"' not in queries[0]["sql"]
    assert '."metadata"' not in queries[0]["sql"]
    assert backfill_embeddings(apply=True)["created"] == 1
    assert vector_identity_gaps(historical.detection.attempt.event, [generation]) == dict(
        eligible=1,
        missing=0,
        divergent=0,
    )


def test_inventory_event_scope_lease_retry_and_private_frozen_generation(historical):
    from datetime import timedelta

    from selfie_search.models import SelfieSearch, SelfieSearchJob

    from processing.models import ProcessingAttempt

    original = historical.detection.attempt
    original.job.status = "retry_wait"
    original.job.save()
    for offset in [-1, 1]:
        ProcessingAttempt.objects.create(
            event=original.event,
            run=original.run,
            job=original.job,
            photo=original.photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            lease_expires_at=timezone.now() + timedelta(hours=offset),
        )
    search = SelfieSearch.objects.create(
        event=original.event,
        public_token_digest="never-print-token",
        temporary_object_key="never-print-key",
        configuration={
            "gallery_face_embedding_generations": [
                {
                    "model": "sface",
                    "contract_version": 1,
                    "processor_type": "face_embedding",
                    "processor_version": 1,
                    "configuration_hash": "a" * 64,
                    "configuration": {"embedding": "never-print-payload"},
                }
            ],
        },
    )
    SelfieSearchJob.objects.create(search=search, status="retry_wait")
    out = StringIO()
    call_command("inspect_pgvector_face_search", event_slug=original.event.slug, stdout=out)
    report = json.loads(out.getvalue())
    assert report["leases"] == dict(active=1, expired=1, unknown=0)
    assert report["retries"]["exhausted"] == 1
    assert report["search_retries"]["ready"] == 1
    assert report["queued_searches"]["rows"][0]["generations"][0]["model"] == "sface"
    assert "never-print" not in out.getvalue()
    assert "private/original" not in out.getvalue()


@pytest.mark.parametrize(("batch_size", "max_rows"), [(0, 1), (1001, 1), (1, 0), (1, 50001)])
def test_backfill_rejects_bounds_without_writing(historical, batch_size, max_rows):
    with pytest.raises(ValueError):
        backfill_embeddings(apply=True, batch_size=batch_size, max_rows=max_rows)
    assert not FaceEmbeddingVector.objects.exists()


def test_scalar_eligibility_has_no_legacy_store_dependency(historical):
    from processing.services.face_cohort import eligible_face_detections

    sql = str(eligible_face_detections().query)
    assert '"processing_faceembedding"' not in sql
    assert '"processing_faceembeddingvector"' not in sql
    generation = dict(
        model="adaface-ir18-webface4m",
        contract_version=1,
        processor_type="face_embedding",
        processor_version=1,
        configuration={},
        configuration_hash="a" * 64,
    )
    assert verify_embeddings(generations=[generation])["groups"][0]["invalid"] == 1
