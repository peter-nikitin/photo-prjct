"""Local HTTP contract proof uses serialized bodies and the real Django endpoints."""

import json
from copy import deepcopy
from datetime import timedelta
from math import sqrt
from typing import Any, cast
from unittest.mock import patch

import pytest
from django.utils import timezone


@pytest.mark.django_db
@pytest.mark.parametrize("dimensions", [128, 512])
def test_maximum_gallery_callback_dual_publication(client, settings, dimensions):
    from processing.models import FaceEmbedding, FaceEmbeddingVector, ProcessingAttempt
    from processing.services.enrollment import (
        FACE_EMBEDDING_QUALITY_CONFIGURATION,
        LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION,
        request_processor,
    )
    from processing.tests import test_views

    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_PROCESSING_FACE_ENABLED = True
    settings.PHOTO_PROCESSING_WORKER_TOKEN = "worker-secret"
    h = test_views.WorkerApiTests()
    h.client = client
    h.setUp()
    photo = h.photo()
    derivative = h.publish_preview(photo)
    version = 5 if dimensions == 512 else 4
    configuration = deepcopy(
        LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION
        if dimensions == 512
        else FACE_EMBEDDING_QUALITY_CONFIGURATION
    )
    request_processor(
        photo,
        processor_type="face_embedding",
        contract_version=3,
        processor_version=version,
        configuration=configuration,
        input_fingerprint={
            "object_key": derivative.final_key,
            "object_size": derivative.byte_size,
            "object_content_type": derivative.content_type,
            "object_etag": None,
            "media_kind": "preview-small-v1",
            "pixel_width": derivative.width,
            "pixel_height": derivative.height,
        },
    )
    with patch("processing.views.ExactPreviewStorage.create_download_grant") as grant:
        grant.return_value.url = "https://storage.example.test/local"
        grant.return_value.expires_at = timezone.now() + timedelta(seconds=30)
        response = h.post(
            "/internal/photo-processing/v1/claim",
            h.face_claim_body(contract_version=3, processor_version=version),
        )
    assert response.status_code == 200
    job = response.json()["job"]
    lease = ProcessingAttempt.objects.get(pk=job["attempt_id"]).lease_expires_at
    face = {
        "status": "kept",
        "index": 0,
        "bbox": [10.0, 20.0, 32.0, 32.0],
        "confidence": 0.95,
        "landmarks": [[1.0, 2.0]] * 5,
        "embedding": [1.0 / sqrt(dimensions)] * dimensions,
        "quality": h.quality_evidence(),
    }
    result = {
        "face_count": 32,
        "model": "adaface-ir18-webface4m" if dimensions == 512 else "sface",
        "faces": [face | {"index": i} for i in range(32)],
        "has_single_query_face_usable": False,
        "warnings": [],
        "timings": {
            "decode_ms": 1,
            "model_load_ms": 2,
            "detect_ms": 3,
            "embed_ms": 4,
            "total_ms": 10,
        },
        "input_geometry": job["input_geometry"],
    }
    body = h.terminal_body(
        job,
        contract_version=3,
        processor_type="face_embedding",
        processor_version=version,
        result=result,
    )
    encoded = json.dumps(body).encode()
    worker_configuration = cast(dict[str, int], configuration["worker"])
    assert len(encoded) < worker_configuration["terminal_result_max_bytes"]
    invalid = deepcopy(body)
    invalid_result = cast(dict[str, Any], invalid["result"])
    invalid_result["faces"][0]["embedding"][0] = 2.0
    rejected = h.post(
        f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", invalid
    )
    assert rejected.status_code == 400
    assert FaceEmbedding.objects.count() == FaceEmbeddingVector.objects.count() == 0
    assert ProcessingAttempt.objects.get(pk=job["attempt_id"]).lease_expires_at == lease
    complete = h.post(f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body)
    assert complete.status_code == 200, complete.json()
    assert FaceEmbedding.objects.count() == FaceEmbeddingVector.objects.count() == 32
    assert ProcessingAttempt.objects.get(pk=job["attempt_id"]).lease_expires_at == lease
    replay = h.post(f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body)
    assert replay.status_code == 200 and replay.json()["idempotent"]
    assert FaceEmbedding.objects.count() == FaceEmbeddingVector.objects.count() == 32


@pytest.mark.django_db
def test_maximum_selfie_callback_cleanup_and_wire(client, settings):
    from picflow.models import Event
    from selfie_search.models import SelfieSearchAttempt
    from selfie_search.services.submission import _configuration

    from processing.tests import test_views

    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_PROCESSING_WORKER_TOKEN = "worker-secret"
    h = test_views.SelfieWorkerApiTests()
    h.client = client
    h.setUp()
    h.event.face_search_generation = Event.FaceSearchGeneration.ADAFACE_V5
    h.event.save()
    h.search.configuration = _configuration(
        event=h.event, content_type="image/jpeg", content_size=1024
    )
    h.search.save()
    h.search.job.configuration = h.search.configuration
    h.search.job.save()
    with patch("processing.views.TemporarySelfieStorage", return_value=h.storage):
        claim = h.post("/internal/photo-processing/v1/claim", h.claim_body())
        assert claim.status_code == 200
        job = claim.json()["job"]
        assert "reader_staff_eligible" not in job and "reader_comparison_requested" not in job
        lease = SelfieSearchAttempt.objects.get(pk=job["attempt_id"]).lease_expires_at
        body = h.success_body(job)
        result = cast(dict[str, object], body["result"])
        result["model"] = "adaface-ir18-webface4m"
        result["embedding"] = [1.0 / sqrt(512)] * 512
        assert len(json.dumps(body).encode()) < 16384
        complete = h.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body
        )
        assert complete.status_code == 200, complete.json()
    h.search.refresh_from_db()
    assert h.search.temporary_object_key == "" and len(h.storage.deleted) == 1
    assert SelfieSearchAttempt.objects.get(pk=job["attempt_id"]).lease_expires_at == lease
