"""Synthetic local transport rehearsal; never a cloud delivery acceptance check."""

import importlib.util
import io
import json
import re
import socket
import ssl
import subprocess
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import Client, override_settings
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import WorkerPool, WorkerPoolMember, WorkerPoolTelemetry
from processing.services import worker_pool_lifecycle as lifecycle
from processing.services.worker_pool_telemetry import generate_diagnostic_metrics, receive
from prometheus_client.parser import text_string_to_metric_families

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]
BUILD = "a" * 40


def probe_module():
    spec = importlib.util.spec_from_file_location(
        "delivery_probe", ROOT / "deploy/worker-pools/telemetry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registered_source(pool="selfie", instance_id="synthetic-node"):
    now = timezone.now()
    lifecycle.configure_pool(pool, group_id="fixture-group", active_build=BUILD)
    lifecycle.record_cloud_snapshot(
        pool,
        group_id="fixture-group",
        sequence=1,
        started_at=now,
        completed_at=now,
        target_size=1,
        members=[{"instance_id": instance_id, "status": "RUNNING_ACTUAL", "worker_build": BUILD}],
        complete=True,
    )
    identity = lifecycle.MemberIdentity(pool, instance_id, uuid4(), BUILD)
    generation = lifecycle.register(identity)["registration_generation"]
    return {
        "pool": pool,
        "instance_id": identity.instance_id,
        "boot_id": str(identity.boot_id),
        "worker_build": BUILD,
        "zone_id": "ru-central1-a",
    }, generation


@pytest.mark.django_db
@override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
def test_status_is_bounded_read_only_and_missing_sources_are_unknown():
    registered_source()
    before = list(WorkerPoolMember.objects.values())
    output = io.StringIO()
    call_command("report_worker_pool_telemetry", "--json", stdout=output)
    report = json.loads(output.getvalue())
    assert report["pools"]["selfie"] == {
        "expected": 1,
        "cloud_fresh": 1,
        "missing": 1,
        "host_fresh": 0,
        "runtime_fresh": 0,
        "scalar_samples": 13,
    }
    assert report["pools"]["bulk"]["expected"] == 0
    assert report["remote_write"] == "unverified"
    assert report["alerts"] == "deferred"
    assert len(output.getvalue()) < 2048
    assert "synthetic-node" not in output.getvalue() and BUILD not in output.getvalue()
    assert before == list(WorkerPoolMember.objects.values())
    assert WorkerPoolTelemetry.objects.count() == 0
    pool = WorkerPool.objects.get()
    pool.observation_completed_at = timezone.now() - timedelta(seconds=91)
    pool.save()
    output = io.StringIO()
    call_command("report_worker_pool_telemetry", "--json", stdout=output)
    assert json.loads(output.getvalue())["pools"]["selfie"]["cloud_fresh"] == 0


@pytest.fixture
def runtime_container(tmp_path):
    check = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=^/findme-photo-worker$", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if check.returncode or check.stdout.strip():
        pytest.skip("Docker unavailable or fixed worker container already exists")
    image = "findme-worker-telemetry-local-web:latest"
    if subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    ).returncode:
        pytest.skip("Reviewed local fixture image absent")
    generation = tmp_path / "generation"
    generation.write_text(str(uuid4()))
    label = "findme.telemetry.delivery=" + str(uuid4())
    program = (
        "from pathlib import Path; "
        "from photo_worker.telemetry import RuntimeTelemetry,start_runtime_server; import time; "
        "t=RuntimeTelemetry(lambda: Path('/fixture/generation').read_text()); "
        "start_runtime_server(t,host='0.0.0.0');\n"
        "while True:\n"
        " t.started('selfie_query'); t.finished('selfie_query','callback_delivered',6.0); "
        "time.sleep(0.1)\n"
    )
    created = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            "findme-photo-worker",
            "--label",
            label,
            "--publish",
            "127.0.0.1::9101",
            "--volume",
            f"{ROOT / 'src/worker'}:/runtime:ro",
            "--volume",
            f"{tmp_path}:/fixture:ro",
            "--env",
            "PYTHONPATH=/runtime",
            "--entrypoint",
            "python",
            image,
            "-c",
            program,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert created.returncode == 0
    container_id = created.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", container_id)
    try:
        port = (
            subprocess.run(
                ["docker", "port", container_id, "9101"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            .stdout.strip()
            .split(":")[-1]
        )
        yield container_id, generation, f"http://127.0.0.1:{port}/metrics"
    finally:
        owned = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "findme.telemetry.delivery"}}',
                container_id,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if owned.returncode == 0 and owned.stdout.strip() == label.split("=", 1)[1]:
            subprocess.run(
                ["docker", "rm", "-f", container_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )


@pytest.mark.django_db(transaction=True)
@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_FLEET_TOKEN="fixture-fleet",
    PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True,
)
def test_local_tls_container_to_receiver_reset_freshness_and_lease_renewal(
    tmp_path, monkeypatch, runtime_container
):
    from processing.contracts import ClaimedJob
    from processing.services.enrollment import request_capture_metadata
    from processing.services.jobs import claim_job, heartbeat_attempt

    probe = probe_module()
    identity, generation = registered_source()
    container_id, generation_file, runtime_url = runtime_container
    generation_file.write_text(generation)
    monkeypatch.setattr(probe, "RUNTIME_URL", runtime_url)
    # Fixture certificate/port/resolution changes are confined to this test process.
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=findme-photo.ru",
            "-addext",
            "subjectAltName=DNS:findme-photo.ru",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=5,
    )
    unavailable = False

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if unavailable:
                self.send_response(503)
                self.end_headers()
                return
            try:
                response = Client().post(
                    self.path,
                    body,
                    content_type="application/json",
                    HTTP_AUTHORIZATION=self.headers["Authorization"],
                    HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
                )
                self.send_response(response.status_code)
                self.end_headers()
                self.wfile.write(response.content)
            finally:
                connections.close_all()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    resolve = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: resolve(
            "127.0.0.1" if host == "findme-photo.ru" else host, *args, **kwargs
        ),
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setattr(
        probe,
        "API",
        f"https://findme-photo.ru:{server.server_port}/internal/photo-processing/v1/members/telemetry",
    )
    collector = probe.Probe(identity, tmp_path / "latest.json")
    try:
        for _ in range(30):
            if probe.scrape_runtime("selfie", timezone.now()) is not None:
                break
            time.sleep(0.1)
        assert collector.step(lambda snapshot: probe.submit(snapshot, "fixture-fleet")) is True
        row = WorkerPoolTelemetry.objects.get()
        assert row.envelope["container"]["container_id"] == container_id
        assert row.envelope["runtime"]["registration_generation"] == generation
        assert row.envelope["runtime"]["aggregates"]["selfie_query"]["callback_delivered"][0] > 0
        first_reset = row.runtime_reset_at
        received_at = row.received_at
        assert (
            collector.retry_pending(lambda snapshot: probe.submit(snapshot, "fixture-fleet"))
            is True
        )
        assert WorkerPoolTelemetry.objects.get().received_at == received_at
        with pytest.raises(ValueError):
            probe.submit(collector.pending, "")
        with pytest.raises(ValueError):
            probe.submit({"extra": "x" * 16384}, "fixture-fleet")
        assert WorkerPoolTelemetry.objects.get().received_at == received_at
        with patch(
            "django.utils.timezone.now", return_value=timezone.now() + timedelta(seconds=91)
        ):
            stale = generate_diagnostic_metrics()
        assert b"worker_host_memory_total_bytes" not in stale
        assert b"worker_runtime_executions_total" not in stale
        event = Event.objects.create(
            name="Synthetic",
            slug="synthetic",
            start_date=date.today(),
            end_date=date.today(),
            timezone_name="Europe/Moscow",
        )
        photo = Photo.objects.create(
            id="synthetic",
            event=event,
            src="",
            original_key="originals/" + "a" * 32,
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=get_user_model().objects.create_user(username="synthetic"),
            original_filename="fixture.jpg",
        )
        request_capture_metadata(photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
            worker_build=BUILD,
            lease_seconds=120,
        )
        assert isinstance(claimed, ClaimedJob)
        unavailable = True
        assert collector.step(lambda snapshot: probe.submit(snapshot, "fixture-fleet")) is False
        assert WorkerPoolTelemetry.objects.get().received_at == received_at
        assert heartbeat_attempt(claimed.attempt.id, lease_seconds=120) is not None
        assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith("json")) == [
            "latest.json"
        ]
        unavailable = False
        new_generation = lifecycle.register(lifecycle.MemberIdentity.parse(identity))[
            "registration_generation"
        ]
        generation_file.write_text(new_generation)
        assert collector.step(lambda snapshot: probe.submit(snapshot, "fixture-fleet")) is True
        assert WorkerPoolTelemetry.objects.get().runtime_reset_at > first_reset
        samples = [
            sample
            for family in text_string_to_metric_families(generate_diagnostic_metrics().decode())
            for sample in family.samples
        ]
        assert all(
            set(sample.labels) <= {"pool", "instance_id", "zone_id", "kind", "outcome", "le"}
            for sample in samples
        )
        output = io.StringIO()
        call_command("report_worker_pool_telemetry", "--json", stdout=output)
        assert json.loads(output.getvalue())["pools"]["selfie"]["host_fresh"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.django_db
@override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
@pytest.mark.parametrize("pool,pairs", [("bulk", 20), ("selfie", 4)])
def test_maximum_snapshot_and_emitted_scalar_cost_inputs(pool, pairs):
    probe = probe_module()
    identity, generation = registered_source(pool, "n" * 64)
    lifecycle.heartbeat(
        lifecycle.MemberIdentity.parse(identity | {"registration_generation": generation}),
        ready=True,
        draining=False,
    )
    now = timezone.now()
    data = identity | {
        "collector_epoch": str(uuid4()),
        "collector_started_at": now.isoformat(),
        "sampled_at": now.isoformat(),
        "sequence": 2**53,
        "host": {
            "cpu_utilization": 0.9999999999999999,
            "memory_available_bytes": float(2**53),
            "memory_total_bytes": float(2**53),
            "root_available_bytes": float(2**53),
            "root_total_bytes": float(2**53),
        },
        "container": {
            "present": True,
            "running": False,
            "events_available": False,
            "container_id": "c" * 64,
            "cpu_usage_cores": float(2**53),
            "cpu_limit_cores": float(2**53),
            "memory_usage_bytes": float(2**53),
            "memory_limit_bytes": float(2**53),
            "restart_count": 2**53,
            "exit_code": 2**53,
            "oom_killed": False,
            "oom_events": 2**53,
            "restart_events": 2**53,
            "events_since": now.isoformat(),
        },
        "runtime": {
            "registration_generation": generation,
            "sampled_at": now.isoformat(),
            "busy": 1,
            "aggregates": {
                kind: {
                    outcome: [2**53, 9007199254740991.0, [2**53] * 8] for outcome in probe.OUTCOMES
                }
                for kind in probe.KINDS[pool]
            },
        },
    }
    raw = probe.encoded(data)
    assert len(raw) < 16384
    receive(json.loads(raw))
    samples = [
        sample
        for family in text_string_to_metric_families(generate_diagnostic_metrics().decode())
        for sample in family.samples
    ]
    assert len(samples) == 38 + pairs * 11
    print(f"cost-input pool={pool} bytes={len(raw)} scalar_samples={len(samples)}")
