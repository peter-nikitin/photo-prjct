from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "deploy/monitoring/probe-vm"


def installer():
    spec = importlib.util.spec_from_file_location("probe_install", PACKAGE / "install.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stage(tmp_path, release):
    import hashlib

    package = tmp_path / release
    package.mkdir()
    files = {
        "monitor_public_health.py": 'print("offline")\n',
        "findme-public-probe.service": (PACKAGE / "findme-public-probe.service").read_text(),
        "findme-public-probe.timer": (PACKAGE / "findme-public-probe.timer").read_text(),
        "install.py": (PACKAGE / "install.py").read_text(),
    }
    for name, content in files.items():
        (package / name).write_text(content)
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "release": release,
                "sha256": {
                    name: hashlib.sha256(content.encode()).hexdigest()
                    for name, content in files.items()
                },
            }
        )
    )
    return package


def test_install_rerun_upgrade_rollback_disable(tmp_path, monkeypatch, capsys):
    module = installer()
    calls = []
    monkeypatch.setattr(module, "systemctl", lambda *args: calls.append(args))
    root = tmp_path / "host"
    first = stage(tmp_path, "a" * 40)
    second = stage(tmp_path, "b" * 40)
    module.apply("install", first, root)
    module.apply("install", first, root)
    base = root / "opt/findme-public-probe"
    assert not (base / "previous").exists()
    module.apply("install", second, root)
    assert (base / "previous").resolve().name == "a" * 40
    module.apply("rollback", None, root)
    assert (base / "current").resolve().name == "a" * 40
    assert (base / "previous").resolve().name == "b" * 40
    module.apply("disable", None, root)
    assert calls[-2:] == [
        ("disable", "--now", "findme-public-probe.timer"),
        ("stop", "findme-public-probe.service"),
    ]
    assert ("enable", "--now", "findme-public-probe.timer") in calls
    assert "PUBLIC_PROBE_TIMER=disabled" in capsys.readouterr().out


def test_tampered_package_never_changes_host(tmp_path, monkeypatch):
    module = installer()
    calls = []
    monkeypatch.setattr(module, "systemctl", lambda *args: calls.append(args))
    package = stage(tmp_path, "a" * 40)
    (package / "monitor_public_health.py").write_text("bad")
    with pytest.raises(ValueError):
        module.apply("install", package, tmp_path / "host")
    assert calls == []
    assert not (tmp_path / "host/opt/findme-public-probe/current").exists()


@pytest.mark.parametrize("failed_command", ["enable", "restart"])
def test_failed_activation_restores_previous_package(tmp_path, monkeypatch, failed_command):
    module = installer()
    monkeypatch.setattr(module, "systemctl", lambda *args: None)
    root = tmp_path / "host"
    module.apply("install", stage(tmp_path, "a" * 40), root)
    failed = False

    def fail_once(*args):
        nonlocal failed
        if args[0] == failed_command and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, "systemctl")

    monkeypatch.setattr(module, "systemctl", fail_once)
    with pytest.raises(subprocess.CalledProcessError):
        module.apply("install", stage(tmp_path, "b" * 40), root)
    assert (root / "opt/findme-public-probe/current").resolve().name == "a" * 40


def test_service_and_workflow_contract():
    service = (PACKAGE / "findme-public-probe.service").read_text()
    timer = (PACKAGE / "findme-public-probe.timer").read_text()
    assert "--auth vm-metadata" in service
    assert "https://findme-photo.ru/health/" in service
    assert "--folder-id b1g2qttgfhb4gdunvlge --check canonical-health" in service
    for setting in (
        "DynamicUser=yes",
        "NoNewPrivileges=yes",
        "MemoryMax=64M",
        "CPUQuota=10%",
        "TimeoutStartSec=90",
    ):
        assert setting in service
    assert "OnUnitActiveSec=5min" in timer
    workflow = (ROOT / ".github/workflows/deploy-public-probe.yml").read_text()
    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "type: choice" in workflow
    for action in ("install", "disable", "rollback"):
        assert f"- {action}" in workflow
    assert "PUBLIC_PROBE_ACTION: ${{ inputs.action }}" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "--consumer public-probe-deploy" in workflow
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text())
    assert manifest["consumers"]["public-probe-deploy"] == ["VM_SSH_KEY"]
    assert (
        "peter-nikitin/photo-prjct/.github/workflows/deploy-public-probe.yml@refs/heads/main"
        in manifest["github_oidc"]["allowed_workflows"]
    )


def test_builder_rejects_non_commit(tmp_path):
    result = subprocess.run(
        [sys.executable, str(PACKAGE / "package.py"), "main", str(tmp_path / "probe.tar")],
        capture_output=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "probe.tar").exists()


def test_builder_uses_only_commit_objects(tmp_path, monkeypatch):
    import tarfile

    spec = importlib.util.spec_from_file_location("probe_package", PACKAGE / "package.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release = "c" * 40
    calls = []

    def git(command, **kwargs):
        calls.append(command)
        if "rev-parse" in command:
            return release + "\n"
        return command[-1].encode()

    monkeypatch.setattr(module.subprocess, "check_output", git)
    output = tmp_path / "package.tar"
    module.build(release, output)
    with tarfile.open(output) as archive:
        assert set(archive.getnames()) == {*module.SOURCES, "manifest.json"}
        probe = archive.extractfile("monitor_public_health.py")
        assert probe and probe.read() == f"{release}:scripts/monitor_public_health.py".encode()
    assert len(calls) == 5


@pytest.mark.parametrize("action", ["install", "disable", "rollback"])
def test_transport_streams_private_package_and_pins_both_hosts(tmp_path, action):
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    release = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    key = tmp_path / "key"
    key.write_text("not-a-real-key")
    key.chmod(0o600)
    env_file = tmp_path / "env"
    env_file.write_text(f'VM_SSH_KEY_FILE="{key}"\n')
    env_file.chmod(0o600)
    python = bin_dir / "python3"
    python.write_text(
        '#!/bin/sh\ncase "$1" in */package.py) printf package > "$3" ;;\n'
        f'*) exec "{sys.executable}" "$@" ;; esac\n'
    )
    ssh = bin_dir / "ssh"
    ssh.write_text(
        '#!/bin/sh\ncp "$2" "$CAPTURE/config"\n'
        'printf "%s" "$4" > "$CAPTURE/remote"\n'
        'cat > "$CAPTURE/package"\n'
        'if [ "$PUBLIC_PROBE_ACTION" = disable ]; then '
        'printf "PUBLIC_PROBE_TIMER=disabled\\n"; exit 0; fi\n'
        'printf "PUBLIC_PROBE_RELEASE=%s\\nPUBLIC_PROBE_TIMER=active\\n" "$PUBLIC_PROBE_RELEASE"\n'
    )
    python.chmod(0o755)
    ssh.chmod(0o755)
    result = subprocess.run(
        ["sh", str(PACKAGE / "run-remote.sh")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PUBLIC_PROBE_RELEASE": release,
            "PUBLIC_PROBE_ACTION": action,
            "FINDME_ENV_FILE": str(env_file),
            "VM_HOST": "192.0.2.1",
            "VM_USER": "tester",
            "VM_SSH_KNOWN_HOSTS": "bastion-key",
            "IMAGE_ORIGIN_VM_HOST": "10.129.0.21",
            "IMAGE_ORIGIN_VM_USER": "tester",
            "IMAGE_ORIGIN_SSH_KNOWN_HOSTS": "origin-key",
            "CAPTURE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    expected_state = "disabled" if action == "disable" else "active"
    assert f"PUBLIC_PROBE_TIMER={expected_state}" in result.stdout
    config = (tmp_path / "config").read_text()
    assert config.count("StrictHostKeyChecking yes") == 2
    assert "ProxyJump probe-bastion" in config
    assert (tmp_path / "package").read_text() == "package"
    remote = (tmp_path / "remote").read_text()
    assert f"action={action}" in remote
    assert '"$stage/install.py" "$action"' in remote
    assert "mktemp -d" in remote and "sudo -n python3" in remote
    assert "compose" not in remote and "apply.sh" not in remote
    assert subprocess.run(["sh", "-n", str(tmp_path / "remote")]).returncode == 0
    assert "not-a-real-key" not in result.stdout + result.stderr


def test_restrictive_umask_keeps_packages_readable_by_dynamic_user(tmp_path, monkeypatch):
    import os
    import stat

    module = installer()
    monkeypatch.setattr(module, "systemctl", lambda *args: None)
    package = stage(tmp_path, "d" * 40)
    previous_umask = os.umask(0o077)
    try:
        module.apply("install", package, tmp_path / "host")
    finally:
        os.umask(previous_umask)
    base = tmp_path / "host/opt/findme-public-probe"
    for directory in (base, base / "releases", base / "current"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert stat.S_IMODE((base / "current/monitor_public_health.py").stat().st_mode) == 0o644


def test_transport_rejects_invalid_action_before_ssh():
    import os

    result = subprocess.run(
        ["sh", str(PACKAGE / "run-remote.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "PUBLIC_PROBE_ACTION": "disable; echo injected"},
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "PUBLIC_PROBE_DEPLOY=error code=invalid_action"
