from datetime import date
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.face_quality import (
    active_face_embedding_generations,
    current_face_embedding_generation,
)
from processing.services.vector_embeddings import (
    persist_accepted_embedding,
    vector_values,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def detection(request):
    event = Event.objects.create(
        name="Vector", slug="vector", start_date=date.today(), end_date=date.today()
    )
    photo = Photo.objects.create(id="vector-photo", event=event, src="photo.jpg")
    generation = (
        active_face_embedding_generations(event)[1]
        if getattr(request, "param", None) == "vector_only"
        else current_face_embedding_generation()
    )
    fields = dict(
        event=event,
        contract_version=3,
        processor_type="face_embedding",
        processor_version=5,
        configuration=generation["configuration"],
        configuration_hash=generation["configuration_hash"],
    )
    run = EventProcessingRun.objects.create(**fields)
    job = ProcessingJob.objects.create(**fields, run=run, photo=photo, input_fingerprint={})
    attempt = ProcessingAttempt.objects.create(
        **{key: value for key, value in fields.items() if key != "configuration_hash"},
        run=run,
        job=job,
        photo=photo,
        input_fingerprint={},
        status="succeeded",
        accepted=True,
        terminal_at=timezone.now(),
    )
    artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
    return PhotoFaceDetection.objects.create(
        attempt=attempt, artifact=artifact, face_index=0, status="kept"
    )


@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
def test_vector_only_publication_retains_metadata_without_json_embedding(detection):
    vector = [1.0] + [0.0] * 511
    native = persist_accepted_embedding(
        detection=detection,
        model_version="adaface-ir18-webface4m",
        vector=vector,
        metadata={"quality": 0.9, "embedding": vector},
    )
    assert isinstance(native, FaceEmbeddingVector)
    assert vector_values(native.vector) == vector
    assert native.metadata == {"quality": 0.9}


@pytest.mark.parametrize("detection", ["vector_only"], indirect=True)
@pytest.mark.parametrize("status", ["quality_rejected", "failed"])
def test_vector_only_publication_rejects_non_kept_faces(detection, status):
    rejected = PhotoFaceDetection.objects.create(
        attempt=detection.attempt, artifact=detection.artifact, face_index=1, status=status
    )
    with pytest.raises(ValueError):
        persist_accepted_embedding(
            detection=rejected,
            model_version="adaface-ir18-webface4m",
            vector=[1.0] + [0.0] * 511,
            metadata={},
        )
    assert not FaceEmbeddingVector.objects.exists()


@pytest.mark.parametrize(("model", "dimensions"), [("adaface-ir18-webface4m", 512)])
def test_native_publication_keeps_identity_metadata_and_values(detection, model, dimensions):
    values = [1.0] + [0.0] * (dimensions - 1)
    row = persist_accepted_embedding(
        detection=detection,
        model_version=model,
        vector=values,
        metadata={"quality": 0.9, "embedding": values},
    )
    assert isinstance(row, FaceEmbeddingVector)
    assert row.model_version == model
    assert row.metadata == {"quality": 0.9}
    assert vector_values(row.vector) == values


@pytest.mark.parametrize(
    ("model", "values"),
    [
        ("unknown", [1.0] + [0.0] * 127),
        ("sface", [1.0] + [0.0] * 511),
        ("adaface-ir18-webface4m", [1.0] + [0.0] * 127),
        ("sface", [0.0] * 128),
        ("sface", [2.0] + [0.0] * 127),
        ("sface", [1.000005] + [0.0] * 127),
        ("sface", [float("nan")] + [0.0] * 127),
        ("sface", [float("inf")] + [0.0] * 127),
        ("sface", [True] + [0.0] * 127),
    ],
)
def test_invalid_input_writes_neither_representation(detection, model, values):
    with pytest.raises(ValueError):
        persist_accepted_embedding(
            detection=detection, model_version=model, vector=values, metadata={}
        )
    assert not FaceEmbeddingVector.objects.exists()


def test_rejected_detection_cannot_publish_vector(detection):
    detection = PhotoFaceDetection.objects.create(
        attempt=detection.attempt,
        artifact=detection.artifact,
        face_index=1,
        status="quality_rejected",
    )
    with pytest.raises(ValueError):
        persist_accepted_embedding(
            detection=detection, model_version="sface", vector=[1.0] + [0.0] * 127, metadata={}
        )
    assert not FaceEmbeddingVector.objects.exists()


@pytest.mark.parametrize("store", ["FaceEmbeddingVector"])
def test_either_write_failure_rolls_back_both_stores(detection, store):
    with patch(
        f"processing.services.vector_embeddings.{store}.objects.create",
        side_effect=IntegrityError("write failed"),
    ):
        with pytest.raises(IntegrityError):
            persist_accepted_embedding(
                detection=detection,
                model_version="adaface-ir18-webface4m",
                vector=[1.0] + [0.0] * 511,
                metadata={},
            )
    assert not FaceEmbeddingVector.objects.exists()


def test_detection_is_unique_and_terminal_vector_is_immutable(detection):
    persist_accepted_embedding(
        detection=detection,
        model_version="adaface-ir18-webface4m",
        vector=[1.0] + [0.0] * 511,
        metadata={},
    )
    row = FaceEmbeddingVector.objects.get(detection=detection)
    with pytest.raises(IntegrityError), transaction.atomic():
        FaceEmbeddingVector.objects.create(
            detection=detection, model_version="adaface-ir18-webface4m", vector=[1.0] + [0.0] * 511
        )
    row.vector = [0.0, 1.0] + [0.0] * 510
    with pytest.raises(ValidationError):
        row.save()


@pytest.mark.parametrize(
    ("model", "values"),
    [("sface", [1.0] + [0.0] * 511), ("sface", [0.0] * 128), ("sface", [2.0] + [0.0] * 127)],
)
def test_database_constraints_protect_bulk_writes(detection, model, values):
    with pytest.raises(IntegrityError), transaction.atomic():
        FaceEmbeddingVector.objects.bulk_create(
            [FaceEmbeddingVector(detection=detection, model_version=model, vector=values)]
        )


def test_database_blocks_mutating_or_deleting_accepted_vector_evidence(detection):
    persist_accepted_embedding(
        detection=detection,
        model_version="adaface-ir18-webface4m",
        vector=[1.0] + [0.0] * 511,
        metadata={},
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        FaceEmbeddingVector.objects.filter(detection=detection).update(
            vector=[0.0, 1.0] + [0.0] * 510
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        FaceEmbeddingVector.objects.filter(detection=detection).delete()


def test_database_rejects_vector_for_quality_rejected_detection(detection):
    rejected = PhotoFaceDetection.objects.create(
        attempt=detection.attempt,
        artifact=detection.artifact,
        face_index=1,
        status="quality_rejected",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        FaceEmbeddingVector.objects.bulk_create(
            [
                FaceEmbeddingVector(
                    detection=rejected, model_version="sface", vector=[1.0] + [0.0] * 127
                )
            ]
        )
