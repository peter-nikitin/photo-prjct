"""Behavioral checks for the disposable worker image builder recipe."""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/worker-pools/image.py"
INSTANCE = "epd" + "a" * 17


def recipe():
    spec = importlib.util.spec_from_file_location("worker_image_recipe", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class BuilderIO:
    def __init__(self, root):
        self.root = root
        (root / "etc").mkdir()
        (root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        (root / "var/tmp").mkdir(parents=True)
        self.calls = []
        self.responses = {
            ("docker", "ps", "-aq"): "",
            ("docker", "image", "ls", "-q"): "",
            ("docker", "volume", "ls", "-q"): "",
            ("docker", "version", "--format", "{{.Server.Version}}"): "29.6.0\n",
            ("docker", "compose", "version", "--short"): "5.1.4\n",
            ("dpkg-query", "-W", "-f=${Version}", "docker-ce"): "5:29.6.0-1~ubuntu.24.04~noble",
            ("dpkg-query", "-W", "-f=${Version}", "docker-ce-cli"): "5:29.6.0-1~ubuntu.24.04~noble",
            (
                "dpkg-query",
                "-W",
                "-f=${Version}",
                "docker-compose-plugin",
            ): "5.1.4-1~ubuntu.24.04~noble",
            ("dpkg-query", "-W", "-f=${Version}", "containerd.io"): "2.2.5-1~ubuntu.24.04~noble",
        }

    def run(self, argv, **kwargs):
        command = tuple(map(str, argv))
        self.calls.append(command)
        if command in self.responses:
            return subprocess.CompletedProcess(argv, 0, self.responses[command], "")
        if command[:2] == ("systemctl", "is-active"):
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        if command[:2] == ("python3", "--version"):
            return subprocess.CompletedProcess(argv, 0, "Python 3.12.3\n", "")
        if command[0].endswith("/opt/findme-worker-telemetry/bin/python") and command[1] == "-c":
            return subprocess.CompletedProcess(argv, 0, "7.1.3 0.25.0\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def get(self, url):
        if url.endswith("/instance/id"):
            return INSTANCE.encode()
        return b"test-package"

    def execute(self, step, **kwargs):
        euid = kwargs.pop("euid", lambda: 0)
        machine = kwargs.pop("machine", lambda: "x86_64")
        return recipe().execute(
            step,
            INSTANCE,
            root=self.root,
            run=self.run,
            get=self.get,
            euid=euid,
            machine=machine,
            **kwargs,
        )


def test_default_plan_is_read_only(tmp_path):
    io = BuilderIO(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    result = io.execute("plan")
    assert result["steps"] == ["prepare", "verify", "seal"]
    assert io.calls == []
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("bad", ["os", "arch", "root", "metadata", "forbidden", "application"])
def test_prepare_rejects_wrong_builder_before_any_mutation(tmp_path, bad):
    io = BuilderIO(tmp_path)
    opts = {}
    if bad == "os":
        (tmp_path / "etc/os-release").write_text('ID=debian\nVERSION_ID="12"\n')
    elif bad == "arch":
        opts["machine"] = lambda: "aarch64"
    elif bad == "root":
        opts["euid"] = lambda: 1000
    elif bad == "metadata":
        io.get = lambda url: b"epd" + b"b" * 17
    elif bad == "forbidden":
        opts["expected_instance_id"] = "epdr5g3p24tdns9890nr"
    else:
        (tmp_path / "etc/findme-worker").mkdir()
    with pytest.raises(ValueError):
        if "expected_instance_id" in opts:
            expected = opts.pop("expected_instance_id")
            recipe().execute(
                "prepare",
                expected,
                root=tmp_path,
                run=io.run,
                get=io.get,
                euid=lambda: 0,
                machine=lambda: "x86_64",
            )
        else:
            io.execute("prepare", **opts)
    assert not (tmp_path / "opt/findme-worker-telemetry").exists()
    assert not any(call[0] == "apt-get" for call in io.calls)


def test_prepare_rejects_docker_objects_without_pruning(tmp_path):
    io = BuilderIO(tmp_path)
    io.responses[("docker", "image", "ls", "-q")] = "sha256:existing\n"
    with pytest.raises(ValueError, match="Docker"):
        io.execute("prepare")
    assert not any("prune" in call for call in io.calls)
    assert not any(call[0] == "apt-get" for call in io.calls)


def test_prepare_rejects_docker_auth_environment_before_mutation(tmp_path, monkeypatch):
    io = BuilderIO(tmp_path)
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "builder credential")
    with pytest.raises(ValueError, match="credential"):
        io.execute("prepare")
    assert not any(call[0] == "apt-get" for call in io.calls)


@pytest.mark.parametrize("name", ["DOCKER_HOST", "DOCKER_CONTEXT"])
def test_prepare_rejects_remote_docker_selection_before_probe(tmp_path, monkeypatch, name):
    io = BuilderIO(tmp_path)
    monkeypatch.setenv(name, "tcp://another-daemon:2375")
    with pytest.raises(ValueError, match="Docker endpoint"):
        io.execute("prepare")
    assert io.calls == []


def test_checksum_mismatch_prevents_apt_or_venv(tmp_path):
    io = BuilderIO(tmp_path)
    with pytest.raises(ValueError, match="SHA256"):
        io.execute("prepare")
    assert not any(call[0] == "apt-get" for call in io.calls)
    assert not (tmp_path / "opt/findme-worker-telemetry").exists()


def test_verified_prepare_installs_exact_local_artifacts_and_existing_telemetry_requirements(
    tmp_path, monkeypatch
):
    module = recipe()
    io = BuilderIO(tmp_path)
    monkeypatch.setattr(
        module,
        "PACKAGES",
        tuple(
            (name, path, hashlib.sha256(b"test-package").hexdigest(), version)
            for name, path, _, version in module.PACKAGES
        ),
    )
    module.execute(
        "prepare",
        INSTANCE,
        root=tmp_path,
        run=io.run,
        get=io.get,
        euid=lambda: 0,
        machine=lambda: "x86_64",
    )
    apt = [call for call in io.calls if call[0] == "apt-get"]
    assert len(apt) == 3  # update, Ubuntu dependencies, verified local Docker packages
    assert all("download.docker.com" not in " ".join(call) for call in apt)
    assert any(call[:3] == ("apt-mark", "hold", "docker-ce") for call in io.calls)
    assert any("telemetry-requirements.txt" in " ".join(call) for call in io.calls)


def test_verify_rejects_runtime_mismatch_and_does_not_write_receipt(tmp_path):
    io = BuilderIO(tmp_path)
    io.responses[("docker", "version", "--format", "{{.Server.Version}}")] = "29.5.0\n"
    with pytest.raises(ValueError, match="Docker"):
        io.execute("verify")
    assert not (tmp_path / "var/lib/findme-worker-image/receipt.json").exists()


def test_verify_rejects_missing_cloud_init_before_seal(tmp_path):
    io = BuilderIO(tmp_path)
    telemetry = tmp_path / "opt/findme-worker-telemetry/bin/python"
    telemetry.parent.mkdir(parents=True)
    telemetry.touch()
    real_run = io.run

    def without_cloud_init(argv, **kwargs):
        if argv == ["cloud-init", "--version"]:
            raise FileNotFoundError("cloud-init")
        return real_run(argv, **kwargs)

    io.run = without_cloud_init
    with pytest.raises(FileNotFoundError):
        io.execute("verify")


def test_verify_rejects_telemetry_dependency_mismatch(tmp_path):
    io = BuilderIO(tmp_path)
    telemetry = tmp_path / "opt/findme-worker-telemetry/bin/python"
    telemetry.parent.mkdir(parents=True)
    telemetry.touch()
    real_run = io.run

    def wrong_telemetry(argv, **kwargs):
        if argv[0] == str(telemetry):
            return subprocess.CompletedProcess(argv, 0, "7.1.2 0.25.0\n", "")
        return real_run(argv, **kwargs)

    io.run = wrong_telemetry
    with pytest.raises(ValueError, match="telemetry"):
        io.execute("verify")


def test_seal_cleans_only_builder_identity_after_verification(tmp_path):
    io = BuilderIO(tmp_path)
    telemetry = tmp_path / "opt/findme-worker-telemetry/bin/python"
    telemetry.parent.mkdir(parents=True)
    telemetry.touch()
    for path in [
        "root/.ssh/authorized_keys",
        "home/ubuntu/.ssh/authorized_keys",
        "etc/ssh/ssh_host_ed25519_key",
    ]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("builder-only")
    retained = tmp_path / "root/.ssh/known_hosts"
    retained.write_text("retain")
    receipt = io.execute("seal", confirm_seal=INSTANCE)
    assert receipt["status"] == "sealed"
    assert receipt["builder_instance_id"] == INSTANCE
    assert len(receipt["recipe_sha256"]) == 64
    assert (tmp_path / "var/lib/findme-worker-image/receipt.json").exists()
    assert (
        json.loads((tmp_path / "var/lib/findme-worker-image/receipt.json").read_text()) == receipt
    )
    assert not (tmp_path / "root/.ssh/authorized_keys").exists()
    assert not (tmp_path / "home/ubuntu/.ssh/authorized_keys").exists()
    assert not (tmp_path / "etc/ssh/ssh_host_ed25519_key").exists()
    assert retained.read_text() == "retain"
    assert io.calls.index(
        ("docker", "version", "--format", "{{.Server.Version}}")
    ) < io.calls.index(
        ("systemctl", "stop", "docker.service", "docker.socket", "containerd.service")
    )
    assert ("cloud-init", "clean", "--logs", "--machine-id", "--seed") in io.calls


def test_failed_seal_leaves_no_success_receipt_or_identity_cleanup(tmp_path):
    io = BuilderIO(tmp_path)
    authorized = tmp_path / "root/.ssh/authorized_keys"
    authorized.parent.mkdir(parents=True)
    authorized.write_text("builder-only")
    io.responses[("docker", "volume", "ls", "-q")] = "customer-volume\n"
    with pytest.raises(ValueError, match="Docker"):
        io.execute("seal", confirm_seal=INSTANCE)
    assert authorized.exists()
    assert not (tmp_path / "var/lib/findme-worker-image/receipt.json").exists()
    assert not any(call[:2] == ("systemctl", "stop") for call in io.calls)


def test_failed_cloud_init_cleanup_never_writes_receipt(tmp_path):
    io = BuilderIO(tmp_path)
    telemetry = tmp_path / "opt/findme-worker-telemetry/bin/python"
    telemetry.parent.mkdir(parents=True)
    telemetry.touch()
    real_run = io.run

    def broken_cleanup(argv, **kwargs):
        if argv == ["cloud-init", "clean", "--logs", "--machine-id", "--seed"]:
            raise subprocess.CalledProcessError(1, argv)
        return real_run(argv, **kwargs)

    io.run = broken_cleanup
    with pytest.raises(subprocess.CalledProcessError):
        io.execute("seal", confirm_seal=INSTANCE)
    assert not (tmp_path / "var/lib/findme-worker-image/receipt.json").exists()


def test_seal_requires_exact_explicit_confirmation(tmp_path):
    io = BuilderIO(tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        io.execute("seal", confirm_seal="yes")
    assert io.calls == []
