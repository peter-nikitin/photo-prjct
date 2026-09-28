import importlib.util
import json
import socket
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from uuid import UUID

import pytest
from photo_worker.telemetry import RuntimeTelemetry

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
IDENTITY = {
    "pool": "selfie",
    "instance_id": "instance-1",
    "boot_id": "12345678-1234-1234-1234-123456789012",
    "worker_build": "a" * 40,
    "zone_id": "ru-central1-a",
}
CONTAINER_ID = "c" * 64
GENERATION = "12345678-1234-1234-1234-123456789013"


def module():
    path = ROOT / "deploy/worker-pools/telemetry.py"
    assert path.exists(), "independent host telemetry probe is absent"
    spec = importlib.util.spec_from_file_location("host_telemetry", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def runtime_text():
    telemetry = RuntimeTelemetry(lambda: GENERATION)
    telemetry.started("selfie_query")
    telemetry.finished("selfie_query", "callback_delivered", 6.0)
    return telemetry.scrape()[1]


def test_runtime_parser_compacts_real_exposition_and_rejects_private_or_unknown_series():
    probe = module()
    parsed = probe.parse_runtime(runtime_text(), GENERATION, "selfie", NOW)
    assert parsed == {
        "registration_generation": GENERATION,
        "sampled_at": NOW.isoformat(),
        "busy": 0,
        "aggregates": {"selfie_query": {"callback_delivered": [1, 6.0, [0, 0, 1, 1, 1, 1, 1, 1]]}},
    }
    for text in (
        runtime_text() + b'private_secret{token="private"} 1\n',
        runtime_text().replace(b'kind="selfie_query"', b'kind="capture_metadata"'),
        runtime_text().replace(b'outcome="callback_delivered"', b'outcome="unknown"'),
        runtime_text().replace(b'kind="selfie_query"', b'kind="selfie_query",photo_id="1"'),
        runtime_text().replace(b"worker_runtime_busy 0.0", b"worker_runtime_busy NaN"),
        runtime_text().replace(b'le="+Inf"', b'le="999"'),
        runtime_text() + b"worker_runtime_busy 0\n",
    ):
        with pytest.raises(ValueError):
            probe.parse_runtime(text, GENERATION, "selfie", NOW)


def test_host_resources_measure_root_pressure_and_keep_failed_fields_unknown(monkeypatch):
    probe = module()
    monkeypatch.setattr(probe.psutil, "cpu_percent", lambda interval: 25.0)
    monkeypatch.setattr(
        probe.psutil, "virtual_memory", lambda: SimpleNamespace(available=50, total=200)
    )
    monkeypatch.setattr(probe.psutil, "disk_usage", lambda path: SimpleNamespace(free=1, total=400))
    assert probe.collect_host() == {
        "cpu_utilization": 0.25,
        "memory_available_bytes": 50,
        "memory_total_bytes": 200,
        "root_available_bytes": 1,
        "root_total_bytes": 400,
    }
    monkeypatch.setattr(probe.psutil, "disk_usage", lambda path: (_ for _ in ()).throw(OSError()))
    assert "root_available_bytes" not in probe.collect_host()


def docker_fixture(*, running=True, oom=False, restarts=2):
    return json.dumps(
        [CONTAINER_ID, running, restarts, oom, 137 if oom else 0, 2_000_000_000, 0, 0, 200]
    )


def test_named_container_crash_oom_and_resources_are_allowlisted():
    probe = module()
    commands = []

    def command(args):
        commands.append(args)
        if args[1] == "inspect":
            return docker_fixture(running=False, oom=True)
        if args[1] == "events":
            return f"{CONTAINER_ID}\toom\t{int(NOW.timestamp()) - 1}\n"
        raise AssertionError(args)

    result = probe.collect_container(NOW - timedelta(seconds=30), NOW, None, command=command)
    assert result["present"] is True and result["running"] is False
    assert result["oom_killed"] is True and result["exit_code"] == 137
    assert result["restart_count"] == 2 and result["oom_events"] == 1
    assert result["events_available"] is False
    assert "cpu_usage_cores" not in result
    assert all("findme-photo-worker" in " ".join(args) for args in commands)
    assert ".State}}" not in " ".join(commands[0])
    assert "{{json .}}" not in " ".join(commands[-1])
    assert "event=oom" in commands[-1] and "event=restart" in commands[-1]


def test_named_container_missing_and_docker_failure_have_distinct_unknown_states():
    probe = module()

    def missing(args):
        if args[1] == "inspect":
            raise subprocess.CalledProcessError(1, args)
        assert args[1:3] == ["ps", "-a"]
        return ""

    assert probe.collect_container(NOW, NOW, None, command=missing) == {
        "present": False,
        "running": False,
        "events_available": False,
    }

    def unavailable(args):
        raise TimeoutError()

    assert probe.collect_container(NOW, NOW, None, command=unavailable) is None


def test_container_stats_and_event_gap_recreation_timeout_do_not_fabricate_zero():
    probe = module()

    def command(args):
        if args[1] == "inspect":
            return docker_fixture()
        if args[1] == "stats":
            return f'"{CONTAINER_ID}"\t"50.00%"\t"50B / 200B"\n'
        raise TimeoutError()

    result = probe.collect_container(NOW - timedelta(seconds=300), NOW, "b" * 64, command=command)
    assert result["cpu_usage_cores"] == 0.5
    assert result["memory_usage_bytes"] == 50 and result["memory_limit_bytes"] == 200
    assert result["cpu_limit_cores"] == 2
    assert result["events_available"] is False
    assert "oom_events" not in result and "restart_events" not in result


def test_subprocess_output_and_time_are_bounded_without_exposing_stderr():
    probe = module()
    with pytest.raises(ValueError):
        probe.bounded_command(
            [sys.executable, "-c", "print('x'*1000000)"], max_output=100, timeout=2
        )
    with pytest.raises(TimeoutError):
        probe.bounded_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        probe.bounded_command(
            [sys.executable, "-c", "import sys; print('secret',file=sys.stderr); sys.exit(1)"]
        )
    assert failure.value.stderr is None and failure.value.output is None


def test_recreated_container_stats_cannot_be_attached_to_preceding_inspect():
    probe = module()

    def command(args):
        if args[1] == "inspect":
            return docker_fixture()
        if args[1] == "stats":
            return f'"{"b" * 64}"\t"50.00%"\t"50B / 200B"\n'
        return ""

    result = probe.collect_container(NOW, NOW, CONTAINER_ID, command=command)
    assert "cpu_usage_cores" not in result and "memory_usage_bytes" not in result
    assert result["events_available"] is False
    assert "oom_events" not in result and "restart_events" not in result


@pytest.mark.parametrize(
    "text, expected", [("1.25KiB", 1280), ("1.25MiB", 1310720), ("1.25kB", 1250)]
)
def test_docker_memory_display_units_preserve_reported_usage(text, expected):
    assert module().memory_bytes(text) == expected


def test_local_actual_oom_and_auto_restart_are_observed_without_control_in_probe():
    probe = module()

    def docker(*args):
        return subprocess.run(
            ["docker", *args], check=True, capture_output=True, text=True, timeout=15
        ).stdout.strip()

    if docker("ps", "-a", "--filter", "name=^/findme-photo-worker$", "--format", "{{.ID}}"):
        pytest.skip("exact worker name is occupied; fixture cannot replace another container")
    identifier = docker(
        "run",
        "--detach",
        "--name",
        "findme-photo-worker",
        "--label",
        "findme.telemetry-test=task3",
        "--memory",
        "64m",
        "--memory-swap",
        "64m",
        "--cpus",
        "0.25",
        "--tmpfs",
        "/pressure:rw,size=128m",
        "--restart",
        "on-failure:1",
        "--entrypoint",
        "sh",
        "nginx:1.27-alpine",
        "-c",
        # Charge touched tmpfs pages to the cgroup; a large malloc can fail in userspace instead.
        "sleep 1; exec dd if=/dev/zero of=/pressure/touched bs=1M count=128",
    )
    assert len(identifier) == 64
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            sampled = datetime.now(UTC)
            observed = probe.collect_container(sampled - timedelta(seconds=60), sampled, identifier)
            if observed and not observed["running"] and observed["restart_count"] == 1:
                break
            time.sleep(0.1)
        assert observed["container_id"] == identifier
        assert observed["running"] is False and observed["oom_killed"] is True
        assert observed["exit_code"] == 137 and observed["restart_count"] == 1
        assert observed["memory_limit_bytes"] == 67108864
        assert observed["cpu_limit_cores"] == 0.25
        assert observed["events_available"] is False
        assert observed["oom_events"] >= 1
    finally:
        # Exact ID returned from this labeled create only; no volumes, image or shared cleanup.
        docker("rm", "--force", identifier)


def test_real_loopback_scrape_caps_body_and_carries_same_generation(monkeypatch):
    probe = module()
    body = runtime_text()
    status, generation = 200, GENERATION

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("X-Worker-Registration-Generation", generation)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(probe, "RUNTIME_URL", f"http://127.0.0.1:{server.server_port}/metrics")
    try:
        assert probe.scrape_runtime("selfie", NOW)["registration_generation"] == GENERATION
        status = 503
        assert probe.scrape_runtime("selfie", NOW) is None
        status, generation = 200, "not-registered"
        assert probe.scrape_runtime("selfie", NOW) is None
        generation = GENERATION
        body = b"x" * 131073
        assert probe.scrape_runtime("selfie", NOW) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_latest_snapshot_persists_sequence_source_times_and_boot_fence_across_receiver_outage(
    tmp_path,
):
    probe = module()
    sent = []

    def unavailable(snapshot):
        sent.append(json.loads(json.dumps(snapshot)))
        raise TimeoutError()

    collector = probe.Probe(IDENTITY, tmp_path / "latest.json", now=lambda: NOW)
    collector.collect = lambda: {"host": {}, "container": None, "runtime": None}
    assert collector.step(unavailable) is False
    original = sent[-1]
    assert original["sequence"] == 1 and original["sampled_at"] == NOW.isoformat()
    assert (tmp_path / "latest.json").stat().st_mode & 0o777 == 0o600
    restarted = probe.Probe(
        IDENTITY, tmp_path / "latest.json", now=lambda: NOW + timedelta(seconds=30)
    )
    restarted.retry_pending(unavailable)
    assert sent[-1] == original
    restarted.collect = lambda: {"host": {}, "container": None, "runtime": None}
    assert restarted.step(unavailable) is False
    assert sent[-1]["sequence"] == 2
    assert sent[-1]["collector_epoch"] == original["collector_epoch"]
    assert sent[-1]["collector_started_at"] == original["collector_started_at"]
    assert len(list(tmp_path.iterdir())) == 1
    assert len((tmp_path / "latest.json").read_bytes()) <= 16384
    rebooted = probe.Probe(
        IDENTITY | {"boot_id": "12345678-1234-1234-1234-123456789099"},
        tmp_path / "latest.json",
        now=lambda: NOW + timedelta(seconds=60),
    )
    rebooted.collect = restarted.collect
    rebooted.step(unavailable)
    assert sent[-1]["sequence"] == 1
    assert UUID(sent[-1]["collector_epoch"]) != UUID(original["collector_epoch"])


def test_tls_submission_has_fixed_target_no_redirect_proxy_override_or_secret_diagnostics(
    monkeypatch,
):
    probe = module()
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, maximum):
            assert maximum == 4097
            return b'{"accepted":true,"duplicate":false}'

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout <= 5
            return Response()

    def opener(*handlers):
        assert any(isinstance(h, probe.ProxyHandler) and h.proxies == {} for h in handlers)
        assert any(isinstance(h, probe.RejectRedirects) for h in handlers)
        return Opener()

    monkeypatch.setattr(probe, "build_opener", opener)
    probe.submit({"sampled_at": NOW.isoformat()}, "fleet-token")
    assert (
        requests[0].full_url
        == "https://findme-photo.ru:8443/internal/photo-processing/v1/members/telemetry"
    )
    assert requests[0].get_header("Authorization") == "Bearer fleet-token"
    assert (
        probe.RejectRedirects().redirect_request(None, None, 302, "", {}, "http://elsewhere")
        is None
    )
    with pytest.raises(ValueError):
        probe.submit({"extra": "x" * 16384}, "fleet-token")


@pytest.mark.parametrize(
    "changes",
    [
        {"zone_id": "anything"},
        {"worker_build": "main"},
        {"pool": "unknown"},
        {"instance_id": "../secret"},
    ],
)
def test_probe_rejects_unbounded_host_identity(tmp_path, changes):
    probe = module()
    with pytest.raises(ValueError):
        probe.Probe(IDENTITY | changes, tmp_path / "latest.json", now=lambda: NOW)


def test_real_tls_submission_validates_ca_hostname_and_rejects_redirect(tmp_path, monkeypatch):
    probe = module()
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
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    received = []
    redirect = False

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, self.headers["Authorization"], json.loads(raw)))
            self.send_response(302 if redirect else 200)
            if redirect:
                self.send_header("Location", "https://redirect.invalid/secret")
            self.end_headers()
            self.wfile.write(b'{"accepted":true,"duplicate":false}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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
            "127.0.0.1" if host in {"findme-photo.ru", "wrong.invalid"} else host, *args, **kwargs
        ),
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    endpoint = f"https://findme-photo.ru:{server.server_port}/internal/photo-processing/v1/members/telemetry"
    monkeypatch.setattr(probe, "API", endpoint)
    try:
        payload = {"sampled_at": NOW.isoformat()}
        probe.submit(payload, "fleet-token")
        assert received == [
            ("/internal/photo-processing/v1/members/telemetry", "Bearer fleet-token", payload)
        ]
        redirect = True
        with pytest.raises(OSError):
            probe.submit(payload, "fleet-token")
        assert len(received) == 2
        redirect = False
        monkeypatch.setattr(probe, "API", endpoint.replace("findme-photo.ru", "wrong.invalid"))
        with pytest.raises(OSError):
            probe.submit(payload, "fleet-token")
        assert len(received) == 2
        monkeypatch.setattr(probe, "API", endpoint)
        monkeypatch.delenv("SSL_CERT_FILE")
        with pytest.raises(OSError):
            probe.submit(payload, "fleet-token")
        assert len(received) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_retry_expired_or_future_source_does_not_submit_or_refresh_time(tmp_path):
    probe = module()
    sent = []
    collector = probe.Probe(IDENTITY, tmp_path / "latest.json", now=lambda: NOW)
    collector.collect = lambda: {"host": {}, "container": None, "runtime": None}
    collector.step(sent.append)
    original = sent[-1].copy()
    collector.now = lambda: NOW + timedelta(seconds=91)
    assert collector.retry_pending(sent.append) is False
    collector.now = lambda: NOW - timedelta(seconds=1)
    assert collector.retry_pending(sent.append) is False
    assert len(sent) == 1 and collector.pending == original


def test_collector_abrupt_exit_during_save_cannot_grow_snapshot_history(tmp_path):
    module()
    source = ROOT / "deploy/worker-pools/telemetry.py"
    program = (
        "import importlib.util, os; from pathlib import Path; "
        f"spec=importlib.util.spec_from_file_location('probe', {str(source)!r}); "
        "probe=importlib.util.module_from_spec(spec); spec.loader.exec_module(probe); "
        f"collector=probe.Probe({IDENTITY!r}, Path({str(tmp_path / 'latest.json')!r})); "
        "collector.collect=lambda: {'host':{}, 'container':None, 'runtime':None}; "
        "os.replace=lambda *args: os._exit(3); collector.step(lambda snapshot: None)"
    )
    result = subprocess.run([sys.executable, "-c", program], timeout=5, capture_output=True)
    assert result.returncode == 3
    probe = module()
    collector = probe.Probe(IDENTITY, tmp_path / "latest.json")
    collector.collect = lambda: {"host": {}, "container": None, "runtime": None}
    collector.step(lambda snapshot: None)
    assert [path.name for path in tmp_path.iterdir()] == ["latest.json"]


def test_maximum_bulk_runtime_envelope_passes_real_receiver_validation(tmp_path):
    from processing.services.worker_pool_telemetry import _validate

    probe = module()
    runtime = RuntimeTelemetry(lambda: GENERATION)
    for kind in (
        "capture_metadata",
        "generate_preview",
        "generate_watermarked_preview",
        "face_embedding",
        "bib_recognition",
    ):
        for outcome in ("callback_delivered", "execution_failed", "transport_failed", "lease_lost"):
            runtime.started(kind)
            runtime.finished(kind, outcome, 1801.0)
    parsed = probe.parse_runtime(runtime.scrape()[1], GENERATION, "bulk", NOW)
    collector = probe.Probe(IDENTITY | {"pool": "bulk"}, tmp_path / "latest.json", now=lambda: NOW)
    collector.collect = lambda: {"host": {}, "container": None, "runtime": parsed}
    snapshots = []
    assert collector.step(snapshots.append) is True
    payload = json.loads(probe.encoded(snapshots[-1]))
    assert len(payload["runtime"]["aggregates"]) == 5
    assert sum(len(pairs) for pairs in payload["runtime"]["aggregates"].values()) == 20
    assert _validate(payload, NOW).pool == "bulk"
