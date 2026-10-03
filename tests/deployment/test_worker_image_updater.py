import importlib.util
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/worker-pools"))
GENERATION = "12345678-1234-1234-1234-123456789013"
IMAGE = "ghcr.io/example/photo-prjct-worker:latest"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"deploy/worker-pools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Docker:
    def __init__(self, *, digest="b", fail=None, running_digest=None):
        self.digest, self.fail, self.calls = digest, fail, []
        self.running_digest = running_digest or digest

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        assert "fleet-token" not in " ".join(args)
        if self.fail and self.fail in args:
            raise subprocess.CalledProcessError(1, args)
        if "up" in args:
            self.running_digest = self.digest
        if args[1:3] == ["container", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "id": "container-" + self.running_digest,
                        "image": "ghcr.io/example/photo-prjct-worker@sha256:"
                        + self.running_digest * 64,
                        "build": self.running_digest * 40,
                        "running": True,
                        "started_at": "2026-10-03T14:00:00Z",
                    }
                )
            )
        if args[1:3] == ["image", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "digest": "ghcr.io/example/photo-prjct-worker@sha256:" + self.digest * 64,
                        "build": self.digest * 40,
                    }
                )
            )
        return SimpleNamespace(stdout="")


def setup_host(tmp_path, *, active=True):
    base = tmp_path / "etc/findme-worker"
    base.mkdir(parents=True)
    (base / "runtime.env").write_text("PHOTO_PROCESSING_FLEET_TOKEN=fleet-token\n")
    if active:
        load("host").save_active(
            {
                "slot": "a",
                "digest": "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64,
                "build": "a" * 40,
                "registration_generation": GENERATION,
            },
            root=tmp_path,
        )
    return base


def test_unchanged_resolved_digest_does_not_replace_container(tmp_path):
    setup_host(tmp_path)
    docker = Docker(digest="a")
    assert load("updater").update(IMAGE, root=tmp_path, run=docker) == "unchanged"
    assert not any("up" in args or "stop" in args for args in docker.calls)


def test_candidate_admission_precedes_active_switch_and_old_drain(tmp_path):
    base = setup_host(tmp_path)
    docker = Docker()

    def ready(port):
        assert port == 9102
        assert json.loads((base / "active.json").read_text())["slot"] == "a"
        assert not any("stop" in args for args in docker.calls)
        return {"ready": True, "registration_generation": GENERATION}

    assert load("updater").update(IMAGE, root=tmp_path, run=docker, status=ready) == "updated"
    active = json.loads((base / "active.json").read_text())
    assert active["slot"] == "b" and active["build"] == "b" * 40
    assert active["digest"].endswith("b" * 64)
    assert any(
        "findme-worker-a" in args and args[-3:] == ["down", "--timeout", "930"]
        for args in docker.calls
    )
    assert (base / "slot-b.env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["pull", "up", "warm"])
def test_failed_candidate_preserves_serving_slot(tmp_path, failure):
    base = setup_host(tmp_path)
    before = (base / "active.json").read_bytes()
    docker = Docker(fail=None if failure == "warm" else failure)

    def ready(port):
        raise OSError("candidate unavailable")

    with pytest.raises((OSError, ValueError, subprocess.SubprocessError)):
        load("updater").update(IMAGE, root=tmp_path, run=docker, status=ready, readiness_seconds=0)
    assert (base / "active.json").read_bytes() == before
    assert not any("down" in args and "findme-worker-a" in args for args in docker.calls)


def test_boot_without_active_slot_starts_current_latest(tmp_path):
    setup_host(tmp_path, active=False)
    docker = Docker()
    assert (
        load("updater").update(
            IMAGE,
            root=tmp_path,
            run=docker,
            status=lambda port: {"ready": True, "registration_generation": GENERATION},
        )
        == "updated"
    )
    assert json.loads((tmp_path / "etc/findme-worker/active.json").read_text())["slot"] == "a"
    assert not any("stop" in args for args in docker.calls)


def test_retirement_and_update_share_exclusive_host_lock(tmp_path):
    host = load("host")
    setup_host(tmp_path)
    docker = Docker()
    with host.host_lock(root=tmp_path):
        with pytest.raises(BlockingIOError):
            load("updater").update(IMAGE, root=tmp_path, run=docker)
    assert docker.calls == []


def test_timer_recovers_candidate_admitted_before_projection(tmp_path):
    base = setup_host(tmp_path)
    host = load("host")
    host.private_json(
        base / "replacement.json",
        {
            "old": host.active_slot(root=tmp_path),
            "candidate": {
                "slot": "b",
                "digest": "ghcr.io/example/photo-prjct-worker@sha256:" + "b" * 64,
                "build": "b" * 40,
            },
        },
    )
    docker = Docker(digest="c", running_digest="b")
    assert (
        load("updater").update(
            IMAGE,
            root=tmp_path,
            run=docker,
            status=lambda port: {"ready": True, "registration_generation": GENERATION},
        )
        == "recovered"
    )
    assert host.active_slot(root=tmp_path)["slot"] == "b"
    assert not (base / "replacement.json").exists()
    assert not any("pull" in args for args in docker.calls)


def test_timer_retries_candidate_start_after_interrupted_docker_submission(tmp_path):
    setup_host(tmp_path)
    with pytest.raises(subprocess.SubprocessError):
        load("updater").update(IMAGE, root=tmp_path, run=Docker(fail="up"))
    docker = Docker()

    def ready(port):
        if not any("findme-worker-b" in args and "up" in args for args in docker.calls):
            raise OSError("candidate has not started")
        return {"ready": True, "registration_generation": GENERATION}

    assert load("updater").update(IMAGE, root=tmp_path, run=docker, status=ready) == "recovered"


def test_failed_corrected_submission_never_admits_previous_image_on_candidate_port(tmp_path):
    base = setup_host(tmp_path)
    updater = load("updater")

    def unavailable(port):
        raise OSError("candidate temporarily unavailable")

    with pytest.raises(ValueError):
        updater.update(IMAGE, root=tmp_path, run=Docker(), status=unavailable, readiness_seconds=0)
    with pytest.raises(subprocess.SubprocessError):
        updater.update(IMAGE, root=tmp_path, run=Docker(digest="c", fail="up"), status=unavailable)

    def ready_b(port):
        return {"ready": True, "registration_generation": GENERATION}

    with pytest.raises(subprocess.SubprocessError):
        updater.update(
            IMAGE,
            root=tmp_path,
            run=Docker(digest="c", running_digest="b", fail="up"),
            status=ready_b,
        )
    assert json.loads((base / "active.json").read_text())["build"] == "a" * 40

    docker = Docker(digest="c", running_digest="b")

    def ready_c(port):
        assert docker.running_digest == "c"
        assert json.loads((base / "active.json").read_text())["slot"] == "a"
        return {"ready": True, "registration_generation": GENERATION}

    assert updater.update(IMAGE, root=tmp_path, run=docker, status=ready_c) == "recovered"
    assert json.loads((base / "active.json").read_text())["build"] == "c" * 40


@pytest.mark.parametrize(
    "reference", [IMAGE, "ghcr.io/example/photo-prjct-worker@sha256:" + "c" * 64]
)
def test_corrected_image_replaces_failed_warm_candidate_without_stopping_old(tmp_path, reference):
    base = setup_host(tmp_path)

    def warming(port):
        if "PHOTO_WORKER_BUILD=" + "c" * 40 not in (base / "slot-b.env").read_text():
            raise OSError("failed candidate is unready")
        assert json.loads((base / "active.json").read_text())["slot"] == "a"
        return {"ready": True, "registration_generation": GENERATION}

    with pytest.raises(ValueError):
        load("updater").update(
            IMAGE, root=tmp_path, run=Docker(), status=warming, readiness_seconds=0
        )
    docker = Docker(digest="c")
    assert (
        load("updater").update(
            reference, root=tmp_path, run=docker, status=warming, readiness_seconds=0
        )
        == "updated"
    )
    assert json.loads((base / "active.json").read_text())["build"] == "c" * 40
    assert not any("up" in args and "findme-worker-a" in args for args in docker.calls)


def test_timer_recovers_after_projection_and_repeats_idempotent_predecessor_cleanup(tmp_path):
    base = setup_host(tmp_path)
    host = load("host")
    old = host.active_slot(root=tmp_path)
    candidate = {
        "slot": "b",
        "digest": "ghcr.io/example/photo-prjct-worker@sha256:" + "b" * 64,
        "build": "b" * 40,
        "registration_generation": GENERATION,
    }
    host.save_active(candidate, root=tmp_path)
    host.private_json(base / "replacement.json", {"old": old, "candidate": candidate})
    docker = Docker()
    assert (
        load("updater").update(
            IMAGE,
            root=tmp_path,
            run=docker,
            status=lambda port: {"ready": True, "registration_generation": GENERATION},
        )
        == "recovered"
    )
    assert any(
        "findme-worker-a" in args and args[-3:] == ["down", "--timeout", "930"]
        for args in docker.calls
    )


def test_retirement_identity_uses_current_slot_generation_and_build(tmp_path, monkeypatch):
    setup_host(tmp_path)
    retire = load("retire")
    monkeypatch.setattr(retire, "active_slot", lambda: load("host").active_slot(root=tmp_path))
    monkeypatch.setattr(
        retire, "status", lambda port: {"ready": True, "registration_generation": GENERATION}
    )
    monkeypatch.setattr(retire, "INSTANCE_PATH", tmp_path / "instance")
    monkeypatch.setattr(retire, "BOOT_PATH", tmp_path / "boot")
    (tmp_path / "instance").write_text("instance-1")
    (tmp_path / "boot").write_text("12345678-1234-1234-1234-123456789012")
    monkeypatch.setenv("PHOTO_WORKER_POOL", "bulk")
    monkeypatch.setenv("PHOTO_WORKER_BUILD", "b" * 40)
    assert retire.own_identity()["worker_build"] == "a" * 40
    assert retire.own_identity()["registration_generation"] == GENERATION


def test_registered_draining_status_still_exposes_generation_for_host_retirement(monkeypatch):
    host = load("host")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"ready": False, "registration_generation": GENERATION}).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(host, "SLOTS", {"a": server.server_port})
    try:
        assert host.status(server.server_port) == {
            "ready": False,
            "registration_generation": GENERATION,
        }
    finally:
        server.shutdown()
        server.server_close()


def test_retirement_committed_grant_survives_worker_endpoint_closing(tmp_path, monkeypatch):
    base = setup_host(tmp_path)
    retire = load("retire")
    active = load("host").active_slot(root=tmp_path)
    boot = "12345678-1234-1234-1234-123456789012"
    (base / "instance-id").write_text("instance-1")
    (base / "boot-id").write_text(boot)
    monkeypatch.setattr(retire, "active_slot", lambda: active)
    monkeypatch.setattr(retire, "host_lock", lambda: load("host").host_lock(root=tmp_path))
    monkeypatch.setattr(retire, "BOOT_PATH", base / "boot-id")
    monkeypatch.setattr(retire, "INSTANCE_PATH", base / "instance-id")
    monkeypatch.setenv("PHOTO_WORKER_POOL", "bulk")
    calls = []

    def status(port):
        assert not calls, "process endpoint may close after granting retirement"
        return {"ready": True, "registration_generation": GENERATION}

    def permission(identity):
        calls.append("granted")
        return {"grant": {"instance_id": "instance-1", "boot_id": boot, "grant_id": GENERATION}}

    monkeypatch.setattr(retire, "status", status)
    monkeypatch.setattr(retire, "permission", permission)
    real_retire = retire.retire
    monkeypatch.setattr(
        retire,
        "retire",
        lambda identity, reply, **kwargs: real_retire(
            identity, reply, run=lambda args, **opts: calls.append(args), **kwargs
        ),
    )
    monkeypatch.setattr(sys, "argv", ["retire.py", "--apply"])
    assert retire.main() == 0
    assert calls[-1] == ["systemctl", "poweroff"]


def test_bootstrap_installs_image_pointer_without_static_build_authority(tmp_path):
    bootstrap = load("bootstrap")
    conf = {
        "pool": "bulk",
        "worker_image": IMAGE,
        "private_api_ipv4": "10.0.0.5",
        "identities": "1/capture_metadata/2",
        "docker_version": "27.5.1",
        "compose_version": "2.32.4",
    }
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="27.5.1" if args[1] == "version" else "2.32.4")

    bootstrap.activate(
        conf,
        {"PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token", "IMAGE_PULL_AUTH": "dXNlcjpwYXNz"},
        "instance-1",
        root=tmp_path,
        run=run,
    )
    env = (tmp_path / "etc/findme-worker/runtime.env").read_text()
    assert "PHOTO_WORKER_BUILD" not in env and "WORKER_IMAGE" not in env
    assert ["systemctl", "start", "findme-worker-updater.service"] in calls
    assert ["systemctl", "enable", "--now", "findme-worker-updater.timer"] in calls
