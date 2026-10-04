"""Functional contract evidence only: synthetic images/results, real TLS/proxy/Django/Postgres."""

import http.client
import importlib.util
import json
import math
import socket
import ssl
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
from uuid import uuid4

import pytest
from django.utils import timezone
from photo_worker.client import ApiError, HttpClient, _RejectApiRedirects
from photo_worker.contracts import FaceEmbeddingFace, FaceEmbeddingResult, SelfieEmbeddingResult
from photo_worker.face_quality import FaceQualityEvidence
from photo_worker.transport import REMOTE_API_URL
from processing.management.commands.control_worker_pools import execute as control
from processing.models import (
    FaceEmbeddingVector,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
    WorkerPool,
)
from processing.services import worker_pool_lifecycle as lifecycle
from processing.services.enrollment import (
    LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION,
    request_processor,
)
from processing.services.worker_pool_metrics import observe_metrics
from processing.tests import test_views as api_fixtures
from processing.tests import test_worker_pool_state_command as state_fixtures
from selfie_search.models import SelfieSearch, SelfieSearchJob
from selfie_search.services.submission import _configuration
from selfie_search.storage import StoredTemporarySelfie

pytestmark = [pytest.mark.operational, pytest.mark.django_db(transaction=True)]
ROOT = Path(__file__).resolve().parents[2]
BUILD = "a" * 40


def acceptance_module():
    spec = importlib.util.spec_from_file_location(
        "worker_pool_acceptance", ROOT / "deploy/worker-pools/acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ceiling", [None, 1, 2])
def test_live_checklist_binds_policy_and_replacement_gates_to_selected_ceiling(ceiling):
    args = [sys.executable, str(ROOT / "deploy/worker-pools/acceptance.py")]
    if ceiling is not None:
        args += ["--pool-max-size", str(ceiling)]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    checklist = json.loads(result.stdout)
    selected = 1 if ceiling is None else ceiling
    assert checklist["live_verified"] is False
    assert checklist["approval_required"] is True
    assert checklist["pool_max_size"] == selected
    assert checklist["scale_bounds"] == {"bulk": [0, selected], "selfie": [1, selected]}
    gates = " ".join(checklist["gates"])
    assert "disk" in gates and "production" in gates
    assert "distinct worker and canonical folders" in gates
    assert "canonical Monitoring folder" in gates
    if selected == 1:
        assert "cap-one Git-owned Managed Prometheus alert" in gates
        assert "serial" in gates and "<=3" in gates
        assert "second-instance demand" not in gates
    else:
        assert "second-instance demand" in gates
        assert "two independent selfie" in gates


def test_functional_entrypoint_runs_existing_policy_and_interrupted_release_fixtures(
    monkeypatch,
):
    acceptance = acceptance_module()
    monkeypatch.setattr(sys, "argv", ["acceptance.py", "--functional-fixture"])
    submitted = []

    def run_fixture(command, *, cwd, check):
        submitted.append((command, cwd, check))
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(acceptance.subprocess, "run", run_fixture)
    assert acceptance.main() == 17
    assert submitted == [
        (
            [
                "make",
                "test",
                "TESTS=-m operational tests/deployment/test_worker_pool_acceptance.py "
                "tests/deployment/test_worker_pool_provisioning.py "
                "tests/deployment/test_component_release.py "
                "tests/deployment/test_worker_image_updater.py "
                "tests/deployment/test_worker_pool_retire.py",
            ],
            ROOT,
            False,
        )
    ]


def run(*args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=40
    ).stdout.strip()


@pytest.fixture
def live_server():
    from pytest_django.live_server_helper import LiveServer

    server = LiveServer("0.0.0.0")
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def tls_proxy(tmp_path, live_server):
    # CA is unique to this fixture. No trust store or machine hostname is modified.
    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-keyout",
        str(tmp_path / "key.pem"),
        "-out",
        str(tmp_path / "cert.pem"),
        "-subj",
        "/CN=findme-photo.ru",
        "-addext",
        "subjectAltName=DNS:findme-photo.ru",
    )
    private = (
        (ROOT / "deploy/nginx/private-worker.conf.template")
        .read_text()
        .replace("/etc/letsencrypt/live/photo-prjct/fullchain.pem", "/fixture/cert.pem")
        .replace("/etc/letsencrypt/live/photo-prjct/privkey.pem", "/fixture/key.pem")
    )
    port = live_server.url.rsplit(":", 1)[1]
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        "events {}\nhttp { upstream django_upstream { server host.docker.internal:"
        + port
        + "; }"
        + private.replace(
            "location / {",
            "location = /internal/photo-processing/v1/redirect { "
            "return 307 https://wrong.invalid/; }\nlocation / {",
        )
        + "}"
    )
    container = run(
        "docker",
        "run",
        "--rm",
        "-d",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-p",
        "127.0.0.1::8443",
        "-v",
        f"{tmp_path}:/fixture:ro",
        "nginx:1.27-alpine",
        "nginx",
        "-c",
        "/fixture/nginx.conf",
        "-g",
        "daemon off;",
    )
    try:
        mapped = int(run("docker", "port", container, "8443").rsplit(":", 1)[1])
        context = ssl.create_default_context(cafile=str(tmp_path / "cert.pem"))

        def opener(*, trusted=True, hostname="findme-photo.ru"):
            trust = context if trusted else ssl.create_default_context()

            class Connection(http.client.HTTPSConnection):
                def connect(self):
                    raw = socket.create_connection(("127.0.0.1", mapped), timeout=self.timeout)
                    self.sock = trust.wrap_socket(raw, server_hostname=hostname)

            class Handler(HTTPSHandler):
                def https_open(self, request):
                    return self.do_open(Connection, request)

            return build_opener(ProxyHandler({}), Handler(), _RejectApiRedirects()).open

        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", mapped), timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        yield opener
    finally:
        run("docker", "rm", "-f", container)


def member(pool, opener):
    lifecycle.configure_pool(pool, group_id=pool + "-group", active_build=BUILD)
    now = timezone.now()
    lifecycle.record_cloud_snapshot(
        pool,
        group_id=pool + "-group",
        sequence=1,
        started_at=now,
        completed_at=now,
        target_size=1,
        complete=True,
        members=[
            {"instance_id": pool + "-node", "status": "RUNNING_ACTUAL", "worker_build": BUILD}
        ],
    )
    lifecycle.record_queue_observation(pool, observed_at=now, endpoint_available=True)
    client = HttpClient(REMOTE_API_URL, "fixture-fleet-only", transport="remote", opener=opener)
    client.bind_member(
        {
            "pool": pool,
            "instance_id": pool + "-node",
            "boot_id": str(uuid4()),
            "worker_build": BUILD,
        }
    )
    client.member_request("register")
    client.member_request("heartbeat", ready=True, draining=False)
    lifecycle.set_claims_paused(pool, paused=False)
    return client


def terminal(claim, result):
    return {
        "job_id": claim.job.id,
        "attempt_id": claim.job.attempt_id,
        "contract_version": claim.job.contract_version,
        "processor_type": claim.job.processor_type,
        "processor_version": claim.job.processor_version,
        "started_at": timezone.now().isoformat().replace("+00:00", "Z"),
        "finished_at": timezone.now().isoformat().replace("+00:00", "Z"),
        "download_ms": 1,
        "compute_ms": 1,
        "total_ms": 2,
        "outcome": "success",
        "result": result,
    }


def test_maximum_face_and_selfie_callback_cross_verified_https_and_persist(tls_proxy, settings):
    settings.ALLOWED_HOSTS = ["findme-photo.ru", "localhost", "testserver"]
    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_PROCESSING_FACE_ENABLED = True
    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = True
    settings.PHOTO_PROCESSING_WORKER_TOKEN = "fixture-local-only"
    settings.PHOTO_PROCESSING_FLEET_TOKEN = "fixture-fleet-only"
    fixture = api_fixtures.WorkerApiTests()
    fixture.setUp()
    fixture.event.face_search_generation = "adaface_v5"
    fixture.event.save(update_fields=["face_search_generation"])
    photo = fixture.photo("functional-tls-photo")
    preview = fixture.publish_preview(photo)
    before_preview = (preview.pk, preview.final_key, preview.accepted_attempt_id)
    request_processor(
        photo,
        processor_type="face_embedding",
        contract_version=3,
        processor_version=5,
        configuration=LOCAL_ADAFACE_FACE_EMBEDDING_CONFIGURATION,
        input_fingerprint={
            "object_key": preview.final_key,
            "object_size": preview.byte_size,
            "object_content_type": preview.content_type,
            "object_etag": None,
            "media_kind": "preview-small-v1",
            "pixel_width": preview.width,
            "pixel_height": preview.height,
        },
    )
    client = member("bulk", tls_proxy())
    grant = Mock(
        url="https://storage.example.test/fixture",
        expires_at=timezone.now() + timedelta(seconds=60),
    )
    with patch("processing.views.ExactPreviewStorage.create_download_grant", return_value=grant):
        claim = client.claim_job(
            lease_seconds=120,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
        )
    embedding = tuple(1 / math.sqrt(512) for _ in range(512))
    timings = {"decode_ms": 1, "model_load_ms": 1, "detect_ms": 1, "embed_ms": 1, "total_ms": 4}

    def face(index):
        return FaceEmbeddingFace(
            index=index,
            bbox=(10.0, 20.0, 32.0, 32.0),
            confidence=0.95,
            landmarks=((1.0, 2.0),) * 5,
            embedding=embedding,
            quality=FaceQualityEvidence(
                algorithm_version="normalized-laplacian-v1",
                crop_size=112,
                confidence=0.95,
                minimum_side_px=32.0,
                relative_area=0.1,
                sharpness=60.0,
                decision="accepted",
                reasons=(),
            ),
        )

    result = FaceEmbeddingResult(
        model="adaface-ir18-webface4m",
        faces=tuple(face(i) for i in range(32)),
        has_single_query_face_usable=False,
        warnings=(),
        timings=timings,
        input_geometry=claim.job.input_geometry,
    ).as_payload()
    payload = terminal(claim, result)
    assert 300_000 < len(json.dumps(payload, separators=(",", ":")).encode()) < 393_216
    assert client.complete(claim.job.attempt_id, payload).status == "succeeded"
    assert (
        FaceEmbeddingVector.objects.filter(detection__attempt_id=claim.job.attempt_id).count() == 32
    )
    preview.refresh_from_db()
    assert (preview.pk, preview.final_key, preview.accepted_attempt_id) == before_preview
    assert client.complete(claim.job.attempt_id, payload).idempotent
    assert (
        FaceEmbeddingVector.objects.filter(detection__attempt_id=claim.job.attempt_id).count() == 32
    )

    search_config = _configuration(
        event=fixture.event, content_type="image/jpeg", content_size=1024
    )
    search = SelfieSearch.objects.create(
        event=fixture.event,
        public_token_digest="f" * 64,
        temporary_object_key="selfie-search/" + "a" * 32,
        configuration=search_config,
    )
    SelfieSearchJob.objects.create(search=search, configuration=search_config)
    selfie = member("selfie", tls_proxy())
    storage = Mock()
    storage.create_download_grant.return_value = grant
    storage.inspect.return_value = StoredTemporarySelfie(
        key=search.temporary_object_key, size=1024, content_type="image/jpeg"
    )
    with patch("processing.views.TemporarySelfieStorage", return_value=storage):
        selfie_claim = selfie.claim_job(
            lease_seconds=120,
            processor_type="selfie_query",
            processor_version=2,
        )
        selfie_result = SelfieEmbeddingResult(
            model="adaface-ir18-webface4m",
            embedding=embedding,
            bbox=(10.0, 20.0, 32.0, 32.0),
            confidence=0.95,
            landmarks=((1.0, 2.0),) * 5,
            timings=timings,
        ).as_payload()
        body = terminal(selfie_claim, selfie_result)
        assert len(json.dumps(body, separators=(",", ":")).encode()) < 16_384
        assert selfie.complete(selfie_claim.job.attempt_id, body).status == "succeeded"
        search.refresh_from_db()
        assert search.status == "ready" and search.results.count() == 1
        saved_results = list(search.results.values())
        assert saved_results[0]["photo_id"] == photo.pk
        assert search.temporary_object_key == ""
        assert selfie.complete(selfie_claim.job.attempt_id, body).idempotent
        assert list(search.results.values()) == saved_results
    search.refresh_from_db()
    assert search.status == "ready"
    assert ProcessingAttempt.objects.get(pk=claim.job.attempt_id).accepted

    for opener in (tls_proxy(trusted=False), tls_proxy(hostname="wrong.invalid")):
        invalid = HttpClient(
            REMOTE_API_URL, "fixture-fleet-only", transport="remote", opener=opener
        )
        with pytest.raises(ApiError, match="network_interruption"):
            invalid.post_json("claim", {})
    for token, path, payload in (
        ("wrong", "claim", {}),
        ("fixture-fleet-only", "redirect", {}),
        ("fixture-fleet-only", "claim", {"padding": "x" * 393_216}),
    ):
        invalid = HttpClient(REMOTE_API_URL, token, transport="remote", opener=tls_proxy())
        with pytest.raises(ApiError):
            invalid.post_json(path, payload)
    assert WorkerPool.objects.count() == 2

    # A previous durable snapshot spans historical terminals, a future retry, a stale
    # attempt, current owned work, and an un-enrolled photo. Only current expired work
    # may change through the current remote-pool recovery authority.
    historical = state_fixtures.WorkerPoolStateCommandTests()
    historical.now = timezone.now()
    historical.event = fixture.event
    historical.photo = photo

    def old_photo(identifier):
        return fixture.photo(identifier, original_key="originals/" + uuid4().hex)

    immutable_ids = []
    for label, job_status, attempt_status in (
        ("success", "succeeded", "succeeded"),
        ("failure", "failed", "failed"),
        ("retry", "retry_wait", "failed"),
        ("stale", "cancelled", "stale"),
    ):
        previous_photo = old_photo("history-" + label)
        job = historical.bulk_job(job_status, due=False, photo=previous_photo)
        attempt = historical.bulk_attempt(job, status=attempt_status, accepted=label == "success")
        immutable_ids.append(attempt.pk)
    never = old_photo("never-enrolled")
    never_states = list(PhotoProcessingState.objects.filter(photo=never).order_by("pk").values())
    assert all(row["status"] == "not_requested" for row in never_states)
    assert not ProcessingJob.objects.filter(photo=never).exists()
    active = historical.bulk_attempt(
        historical.bulk_job("processing", photo=old_photo("active-remote"))
    )
    expired = historical.bulk_attempt(
        historical.bulk_job("processing", photo=old_photo("expired-remote")), expired=True
    )
    before_attempts = list(
        ProcessingAttempt.objects.filter(pk__in=immutable_ids).order_by("pk").values()
    )
    job_ids = [row["job_id"] for row in before_attempts]
    before_jobs = list(ProcessingJob.objects.filter(pk__in=job_ids).order_by("pk").values())
    control({"operation": "recover", "pool": "bulk"})
    expired.refresh_from_db()
    assert expired.status == "expired"
    active.refresh_from_db()
    assert active.status == "in_progress"
    # Inject host loss, not a real stop: all observed bulk machines disappear and its
    # remaining lease expires. Read-only metrics must retain recoverable demand at zero.
    ProcessingAttempt.objects.filter(pk=active.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    now = timezone.now()
    lifecycle.record_cloud_snapshot(
        "bulk",
        group_id="bulk-group",
        sequence=2,
        started_at=now,
        completed_at=now,
        target_size=0,
        members=[],
        complete=True,
    )
    metrics = observe_metrics("ru-central1-a")["metrics"]
    assert (
        next(
            row["value"]
            for row in metrics
            if row["name"] == "worker_pool_recoverable_leases" and row["labels"]["pool"] == "bulk"
        )
        == 1
    )
    WorkerPool.objects.filter(name="bulk").update(queue_observed_at=now - timedelta(seconds=91))
    assert not control({"operation": "status"})["bulk"]["fresh"]
    control({"operation": "recover", "pool": "bulk"})
    active.refresh_from_db()
    assert active.status == "expired"
    assert (
        list(ProcessingAttempt.objects.filter(pk__in=immutable_ids).order_by("pk").values())
        == before_attempts
    )
    assert list(ProcessingJob.objects.filter(pk__in=job_ids).order_by("pk").values()) == before_jobs
    assert (
        list(PhotoProcessingState.objects.filter(photo=never).order_by("pk").values())
        == never_states
    )
    assert not ProcessingJob.objects.filter(photo=never).exists()
    preview.refresh_from_db()
    assert (preview.pk, preview.final_key, preview.accepted_attempt_id) == before_preview
