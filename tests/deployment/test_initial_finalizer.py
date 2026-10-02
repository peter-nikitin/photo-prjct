import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.deployment.test_worker_pool_release import release_module

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]


def module():
    spec = importlib.util.spec_from_file_location(
        "finalize_initial", ROOT / "deploy/worker-pools/finalize_initial.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture(tmp_path):
    finalizer = module()
    release = release_module()
    config = {
        "worker_build": finalizer.BUILD,
        "worker_image": "worker@" + finalizer.DIGEST,
        "canonical_vm_id": "canonical",
        "pool_max_size": 1,
        "groups": {name: {"id": value} for name, value in finalizer.GROUPS.items()},
    }
    candidate = {
        "manifest": {"configuration": config},
        "proof": {"web_image": "web@sha256:123", "web_id": "sha256:123"},
    }
    receipt = release.Journal(
        tmp_path / "worker-pools-release.json",
        {
            "phase": "verified",
            "previous": None,
            "verified": ["bulk", "selfie"],
            "pending": None,
            "candidate": candidate,
        },
    )
    package = tmp_path / ".deployment-previous.original"
    package.mkdir()
    (package / "original").write_text("original")
    recovery = tmp_path / ".deployment-recovery"
    recovery.mkdir()
    for name, value in {
        "package-path": str(package) + "\n",
        "previous.env": "secret",
        "deployed-image": "old",
        "worker-topology": "local",
    }.items():
        (recovery / name).write_text(value)
    (tmp_path / "deployed-image").write_text("web:" + finalizer.BUILD + "\n")
    snapshot = {
        name: {
            "group_id": value,
            "claims_paused": False,
            "local_claims_paused": True,
            "local_live_attempts": 0,
            "live_attempts": 0,
            "members": [],
        }
        for name, value in finalizer.GROUPS.items()
    }
    host = SimpleNamespace(control=lambda op: snapshot, verify_web=Mock())
    release.canonical_instance_id = lambda: "canonical"
    release.validate_manifest = Mock()
    release.verify_image = Mock()
    release.verify_fleet = Mock()
    return finalizer, release, receipt, host, package, recovery


@pytest.mark.parametrize(
    "change", ["build", "group", "phase", "pending", "leases", "marker", "symlink"]
)
def test_initial_finalizer_rejects_drift_before_commit(tmp_path, change):
    finalizer, release, receipt, host, package, recovery = fixture(tmp_path)
    if change == "build":
        receipt.data["candidate"]["manifest"]["configuration"]["worker_build"] = "a" * 40
    elif change == "group":
        receipt.data["candidate"]["manifest"]["configuration"]["groups"]["bulk"]["id"] = "wrong"
    elif change == "phase":
        receipt.data["phase"] = "prepared"
    elif change == "pending":
        receipt.data["pending"] = {"operation": "unknown"}
    elif change == "leases":
        host.control("status")["bulk"]["local_live_attempts"] = 1
    elif change == "marker":
        (tmp_path / "worker-pools-current.json").write_text("{}")
    else:
        package.rename(tmp_path / "target")
        package.symlink_to(tmp_path / "target", target_is_directory=True)
    receipt.save()
    with pytest.raises(ValueError):
        finalizer.finalize(tmp_path, release, host, gates=Mock())
    assert recovery.exists()
    assert release.Journal(receipt.path).data["phase"] != "committed"


def test_precommit_health_failure_preserves_recovery(tmp_path):
    finalizer, release, receipt, host, package, recovery = fixture(tmp_path)
    with pytest.raises(ValueError, match="health"):
        finalizer.finalize(tmp_path, release, host, gates=Mock(side_effect=ValueError("health")))
    assert release.Journal(receipt.path).data["phase"] == "verified"
    assert package.exists() and recovery.exists()


def test_committed_cleanup_retry_does_not_repeat_commit(tmp_path, monkeypatch):
    finalizer, release, receipt, host, package, recovery = fixture(tmp_path)
    cleanup = finalizer.cleanup_owned
    monkeypatch.setattr(finalizer, "cleanup_owned", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(OSError):
        finalizer.finalize(tmp_path, release, host, gates=Mock())
    assert release.Journal(receipt.path).data["phase"] == "committed"
    release.commit_release = Mock(side_effect=AssertionError("second commit"))
    monkeypatch.setattr(finalizer, "cleanup_owned", cleanup)
    finalizer.finalize(
        tmp_path, release, host, gates=Mock(side_effect=AssertionError("new health"))
    )
    assert not package.exists() and not recovery.exists()
    with pytest.raises(ValueError, match="already finalized"):
        finalizer.finalize(tmp_path, release, host, gates=Mock())


def test_committed_cleanup_rejects_changed_marker(tmp_path, monkeypatch):
    finalizer, release, receipt, host, package, recovery = fixture(tmp_path)
    monkeypatch.setattr(finalizer, "cleanup_owned", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(OSError):
        finalizer.finalize(tmp_path, release, host, gates=Mock())
    (tmp_path / "worker-pools-current.json").write_text("{}")
    with pytest.raises(ValueError, match="committed marker"):
        finalizer.finalize(tmp_path, release, host, gates=Mock())
    assert package.exists() and recovery.exists()


@pytest.mark.parametrize("gate", ["fleet", "web", "worker-image"])
def test_live_proof_failure_preserves_recovery(tmp_path, gate):
    finalizer, release, receipt, host, package, recovery = fixture(tmp_path)
    failure = Mock(side_effect=ValueError("proof failed"))
    if gate == "fleet":
        release.verify_fleet = failure
    elif gate == "web":
        host.verify_web = failure
    else:
        release.verify_image = failure
    with pytest.raises(ValueError, match="proof failed"):
        finalizer.finalize(tmp_path, release, host, gates=Mock())
    assert release.Journal(receipt.path).data["phase"] == "verified"
    assert package.exists() and recovery.exists()


@pytest.mark.parametrize("gate", ["collector-stale", "collector-failed", "private", "probe"])
def test_health_gates_fail_closed(tmp_path, gate, monkeypatch):
    finalizer = module()
    monkeypatch.setattr(finalizer.time, "time", lambda: 1000)

    def run(command, **kwargs):
        if command[:2] == ["systemctl", "show"]:
            return SimpleNamespace(
                stdout="Result="
                + ("failed" if gate == "collector-failed" else "success")
                + "\nExecMainStatus=0\nExecMainExitTimestamp=recent\n"
            )
        if command[0] == "date":
            return SimpleNamespace(stdout="800" if gate == "collector-stale" else "999")
        if command[0] == "curl":
            return SimpleNamespace(stdout="503" if gate == "private" else "401")
        if command[0] == "docker" and command[1] == "inspect":
            return SimpleNamespace(stdout="journald|findme.service=" + command[-1])
        if "ps" in command:
            return SimpleNamespace(stdout=command[-1])
        if "verify-probe" in command and gate == "probe":
            raise ValueError("missing probe")
        return SimpleNamespace(stdout="")

    with pytest.raises(ValueError):
        finalizer.health_gates(tmp_path, SimpleNamespace(run=run, compose=["docker", "compose"]))


def test_workflow_finalizer_bypasses_deployment_jobs():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    jobs = workflow["jobs"]
    finalizer = jobs["finalize-initial-workers"]
    assert "needs" not in finalizer
    assert finalizer["concurrency"] == jobs["deploy"]["concurrency"]
    for name, job in jobs.items():
        if name != "finalize-initial-workers":
            assert "!inputs.finalize_initial_workers" in job["if"]
    command = finalizer["steps"][-1]["run"]
    assert "finalize-initial-workers" in command
    assert "package-deployment" not in command and "build" not in command


def test_finalizer_lock_contention_stops_before_release_loading(tmp_path, monkeypatch):
    import fcntl
    import hashlib
    import sys

    finalizer = module()
    source = ROOT / "deploy/worker-pools/finalize_initial.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(source),
            "--source-sha256",
            hashlib.sha256(source.read_bytes()).hexdigest(),
            "--expected-web-sha",
            finalizer.BUILD,
            "--expected-worker-digest",
            finalizer.DIGEST,
            "--expected-bulk-group",
            finalizer.GROUPS["bulk"],
            "--expected-selfie-group",
            finalizer.GROUPS["selfie"],
        ],
    )
    monkeypatch.setattr(
        finalizer, "Path", lambda value: tmp_path if value == "/opt/photo-prjct" else Path(value)
    )
    with (tmp_path / ".deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            finalizer.main()
