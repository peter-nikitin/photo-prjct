"""Local HTTP contract proof uses serialized bodies and the real Django endpoints."""

import json
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from math import sqrt
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone


def _remote_session(client, settings, pool):
    """Seed trusted cloud inventory, then register through the actual private API."""
    from processing.services import worker_pool_lifecycle as lifecycle

    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = True
    settings.PHOTO_PROCESSING_FLEET_TOKEN = "fleet-secret"
    build = "a" * 40
    now = timezone.now()
    lifecycle.configure_pool(pool, group_id=pool + "-group", active_build=build)
    lifecycle.record_cloud_snapshot(
        pool,
        group_id=pool + "-group",
        sequence=1,
        started_at=now,
        completed_at=now,
        target_size=1,
        members=[
            {"instance_id": pool + "-node", "status": "RUNNING_ACTUAL", "worker_build": build}
        ],
        complete=True,
    )
    lifecycle.record_queue_observation(pool, observed_at=now, endpoint_available=True)
    lifecycle.set_claims_paused(pool, paused=False)
    headers = {
        "HTTP_AUTHORIZATION": "Bearer fleet-secret",
        "HTTP_X_FINDME_WORKER_TRANSPORT": "private-tls",
    }
    envelope = {
        "pool": pool,
        "instance_id": pool + "-node",
        "boot_id": str(uuid4()),
        "worker_build": build,
    }
    base = "/internal/photo-processing/v1/members/"
    registration = client.post(
        base + "register", envelope, content_type="application/json", **headers
    )
    assert registration.status_code == 200, registration.content
    envelope["registration_generation"] = registration.json()["registration_generation"]
    heartbeat = client.post(
        base + "heartbeat",
        envelope | {"ready": True, "draining": False},
        content_type="application/json",
        **headers,
    )
    assert heartbeat.status_code == 200, heartbeat.content
    return envelope, headers


@pytest.mark.django_db
@pytest.mark.parametrize(
    "dimensions,remote,vector_only",
    [(128, False, False), (512, False, False), (512, True, False), (512, True, True)],
)
def test_maximum_gallery_callback_publication(client, settings, dimensions, remote, vector_only):
    from photo_worker.client import HttpClient
    from photo_worker.contracts import Claim
    from photo_worker.runner import _success_payload

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
    settings.DEBUG = True
    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = False
    h = test_views.WorkerApiTests()
    h.client = client
    h.setUp()
    envelope = {}
    if remote:
        envelope, h.headers = _remote_session(client, settings, "bulk")
    photo = h.photo()
    derivative = h.publish_preview(photo)
    version = 5 if dimensions == 512 else 4
    configuration = deepcopy(
        LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION
        if dimensions == 512
        else FACE_EMBEDDING_QUALITY_CONFIGURATION
    )
    if vector_only:
        configuration["embedding_storage"] = "vector_only"
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
            h.face_claim_body(contract_version=3, processor_version=version) | envelope,
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
    worker_job = Claim.from_response(response.json()).job
    assert worker_job is not None
    body = _success_payload(
        worker_job, "2026-07-29T10:00:00Z", "2026-07-29T10:00:03Z", result, 1, 2, 3
    )
    assert "worker_build" not in body
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

    def opener(request, *, timeout):
        wire = client.post(
            urlsplit(request.full_url).path,
            request.data,
            content_type=request.get_header("Content-type"),
            **h.headers,
        )
        assert wire.status_code == 200, wire.content
        return BytesIO(wire.content)

    worker_client = HttpClient(
        "https://worker.test/internal/photo-processing/v1", "worker-secret", opener=opener
    )
    complete = worker_client.complete(job["attempt_id"], body)
    assert complete.status == "succeeded" and not complete.stale
    assert FaceEmbedding.objects.count() == (0 if vector_only else 32)
    assert FaceEmbeddingVector.objects.count() == 32
    assert ProcessingAttempt.objects.get(pk=job["attempt_id"]).lease_expires_at == lease
    replay = h.post(f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body)
    assert replay.status_code == 200 and replay.json()["idempotent"]
    assert FaceEmbedding.objects.count() == (0 if vector_only else 32)
    assert FaceEmbeddingVector.objects.count() == 32


@pytest.mark.django_db
def test_remote_selfie_callback_uses_enabled_vector_reader_and_keeps_results_immutable(
    client, settings
):
    from feature_flags.models import FeatureFlag
    from feature_flags.registry import PGVECTOR_FACE_SEARCH_READ
    from selfie_search.models import SelfieSearch, SelfieSearchAttempt
    from selfie_search.tests.test_jobs import SearchJobTests

    from processing.models import FaceEmbedding, FaceEmbeddingVector, WorkerPoolMember
    from processing.tests.test_views import SelfieWorkerApiTests, SelfieWorkerStorage

    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_PROCESSING_FACE_ENABLED = True
    settings.PHOTO_PROCESSING_WORKER_TOKEN = "local-secret"
    fixture = SearchJobTests()
    fixture.setUp()
    search = fixture.make_search()
    for embedding in FaceEmbedding.objects.all():
        FaceEmbeddingVector.objects.create(
            detection=embedding.detection,
            model_version=embedding.model_version,
            vector=embedding.vector,
        )
    flag = FeatureFlag.objects.create(key=PGVECTOR_FACE_SEARCH_READ.key, state="on")
    call_command("sync_feature_flags")
    flag.refresh_from_db()
    assert flag.state == "on"
    wire = SelfieWorkerApiTests()
    wire.client = client
    envelope, wire.headers = _remote_session(client, settings, "selfie")
    storage = SelfieWorkerStorage()
    with patch("processing.views.TemporarySelfieStorage", return_value=storage):
        claim = wire.post("/internal/photo-processing/v1/claim", wire.claim_body() | envelope)
        assert claim.status_code == 200, claim.content
        job = claim.json()["job"]
        member = WorkerPoolMember.objects.get(instance_id="selfie-node")
        assert str(member.active_selfie_attempt_id) == job["attempt_id"]
        body = wire.success_body(job)
        assert len(json.dumps(body).encode()) < 16384
        with CaptureQueriesContext(connection) as queries:
            complete = wire.post(
                f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body
            )
        assert complete.status_code == 200, complete.content
        assert any("<=>" in row["sql"] for row in queries.captured_queries)
        search.refresh_from_db()
        assert search.status == SelfieSearch.Status.READY
        assert search.temporary_object_key == "" and len(storage.deleted) == 1
        assert search.results.count() == 1
        saved = list(search.results.values())
        assert saved[0]["photo_id"] == "candidate-0"
        assert SelfieSearchAttempt.objects.get(pk=job["attempt_id"]).status == "succeeded"
        replay = wire.post(
            f"/internal/photo-processing/v1/attempts/{job['attempt_id']}/complete", body
        )
        assert replay.status_code == 200 and replay.json()["idempotent"]
        assert list(search.results.values()) == saved
        assert len(storage.deleted) == 1
        flag.refresh_from_db()
        assert flag.state == "on"


@pytest.mark.django_db
def test_maximum_selfie_callback_cleanup_and_wire(client, settings):
    from picflow.models import Event
    from selfie_search.models import SelfieSearchAttempt
    from selfie_search.services.submission import _configuration

    from processing.tests import test_views

    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_PROCESSING_WORKER_TOKEN = "worker-secret"
    settings.DEBUG = True
    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = False
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
