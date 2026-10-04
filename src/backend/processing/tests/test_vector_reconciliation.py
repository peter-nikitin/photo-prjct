import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from processing.models import FaceEmbeddingVector, PhotoFaceEmbeddingProjection
from processing.services.vector_reconciliation import vector_identity_gaps, verify_embeddings
from processing.tests.test_vector_embeddings import detection  # noqa: F401

pytestmark = pytest.mark.django_db


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


def generation(source_detection, model="sface"):
    attempt = source_detection.attempt
    return dict(
        model=model,
        contract_version=attempt.contract_version,
        processor_type=attempt.processor_type,
        processor_version=attempt.processor_version,
        configuration=attempt.configuration,
        configuration_hash=attempt.job.configuration_hash,
    )


@pytest.mark.parametrize(("model", "dimensions"), [("sface", 128), ("adaface-ir18-webface4m", 512)])
def test_native_verifier_accepts_both_models_without_json(native_detection, model, dimensions):
    FaceEmbeddingVector.objects.create(
        detection=native_detection, model_version=model, vector=[1.0] + [0.0] * (dimensions - 1)
    )
    with CaptureQueriesContext(connection) as queries:
        report = verify_embeddings(
            native_detection.attempt.event, [generation(native_detection, model)]
        )
    assert report["groups"][0] == dict(
        event=str(native_detection.attempt.event_id),
        model=model,
        eligible=1,
        missing=0,
        invalid=0,
        divergent=0,
    )
    assert all('"processing_faceembedding"' not in query["sql"] for query in queries)
    call_command(
        "verify_pgvector_face_embeddings",
        event_slug=native_detection.attempt.event.slug,
        stdout=StringIO(),
    )


@pytest.mark.parametrize("native_model", [None, "adaface-ir18-webface4m"])
def test_native_verifier_rejects_missing_or_wrong_model(native_detection, native_model):
    if native_model:
        FaceEmbeddingVector.objects.create(
            detection=native_detection, model_version=native_model, vector=[1.0] + [0.0] * 511
        )
    report = verify_embeddings(native_detection.attempt.event, [generation(native_detection)])
    assert sum(report["groups"][0][field] for field in ("missing", "invalid", "divergent")) == 1


@pytest.mark.parametrize("vector", [[1.0], [0.0] * 128, [float("nan")] + [0.0] * 127])
def test_native_verifier_detects_invalid_payloads(native_detection, vector):
    from processing.services import vector_reconciliation

    rows = [
        dict(
            id=native_detection.pk,
            attempt__event_id=native_detection.attempt.event_id,
            attempt__contract_version=1,
            attempt__processor_version=1,
            attempt__job__configuration_hash="a" * 64,
            attempt__job__configuration={},
            attempt__configuration={},
            attempt__run__configuration={},
            embedding_vector__id=native_detection.pk,
            embedding_vector__model_version="sface",
            embedding_vector__vector=vector,
        )
    ]
    # DB constraints prevent insertion; emulate driver rows from an older schema.
    with patch.object(vector_reconciliation, "iter_reconciliation_rows", return_value=iter(rows)):
        assert verify_embeddings()["groups"][0]["invalid"] == 1


def test_scalar_identity_gaps_are_native_and_do_not_select_payloads(native_detection):
    generations = [generation(native_detection)]
    with CaptureQueriesContext(connection) as queries:
        assert vector_identity_gaps(native_detection.attempt.event, generations) == dict(
            eligible=1, missing=1, divergent=0
        )
    assert len(queries) == 1
    assert '"processing_faceembedding"' not in queries[0]["sql"]
    assert '."vector"' not in queries[0]["sql"]
    FaceEmbeddingVector.objects.create(
        detection=native_detection, model_version="sface", vector=[1.0] + [0.0] * 127
    )
    assert vector_identity_gaps(native_detection.attempt.event, generations) == dict(
        eligible=1, missing=0, divergent=0
    )


def test_hidden_native_evidence_is_inactive(native_detection):
    FaceEmbeddingVector.objects.create(
        detection=native_detection, model_version="sface", vector=[1.0] + [0.0] * 127
    )
    photo = native_detection.attempt.photo
    photo.is_hidden = True
    photo.save()
    assert verify_embeddings() == dict(groups=[], inactive=1)


def test_native_inventory_preserves_private_aggregate_output(native_detection):
    out = StringIO()
    call_command(
        "inspect_pgvector_face_search", event_slug=native_detection.attempt.event.slug, stdout=out
    )
    report = json.loads(out.getvalue())
    assert report["ready"]
    assert "legacy_rows" not in report
    assert "private/original" not in out.getvalue()


def test_scalar_command_requires_event_and_rejects_gaps(native_detection):
    with pytest.raises(CommandError):
        call_command("verify_pgvector_face_embeddings", scalar=True, stdout=StringIO())
    with pytest.raises(CommandError):
        call_command(
            "verify_pgvector_face_embeddings",
            scalar=True,
            event_slug=native_detection.attempt.event.slug,
            stdout=StringIO(),
        )
    FaceEmbeddingVector.objects.create(
        detection=native_detection, model_version="sface", vector=[1.0] + [0.0] * 127
    )
    out = StringIO()
    call_command(
        "verify_pgvector_face_embeddings",
        scalar=True,
        event_slug=native_detection.attempt.event.slug,
        stdout=out,
    )
    assert json.loads(out.getvalue())["groups"][0]["eligible"] == 1


@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
def test_scalar_native_gate_rejects_wrong_model_for_frozen_generation(native_detection):
    FaceEmbeddingVector.objects.create(
        detection=native_detection,
        model_version="sface",
        vector=[1.0] + [0.0] * 127,
    )
    with pytest.raises(CommandError):
        call_command(
            "verify_pgvector_face_embeddings",
            scalar=True,
            event_slug=native_detection.attempt.event.slug,
            stdout=StringIO(),
        )
