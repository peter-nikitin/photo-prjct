import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_starts_only_web_stack(tmp_path: Path) -> None:
    commands = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMANDS"\n')
    docker.chmod(0o755)
    (tmp_path / ".env").write_text("APP_IMAGE=ghcr.io/example/app@sha256:" + "a" * 64 + "\n")
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "COMMANDS": str(commands),
            "DEPLOY_ROOT": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = commands.read_text()
    assert "manage.py check --tag commerce_runtime" in calls
    assert calls.index("manage.py check --tag commerce_runtime") < calls.index("up -d --wait db")
    assert "up -d --wait db" in calls
    assert "up -d --wait web" in calls
    assert "up -d --wait --force-recreate --no-deps nginx" in calls
    assert "--profile photo-worker pull photo-worker" in calls
    assert "up -d --wait photo-worker" not in calls


def test_transport_sends_secrets_only_in_stdin(tmp_path: Path, monkeypatch) -> None:
    import json
    import runpy

    module = runpy.run_path(str(ROOT / "deploy/run-single-vm.py"))
    key = tmp_path / "key"
    key.write_text("private key")
    key.chmod(0o600)
    env_file = tmp_path / "private.env"
    env_file.write_text(
        f'VM_SSH_KEY_FILE="{key}"\nGHCR_READ_TOKEN="registry-secret"\nSECRET_KEY="django-secret"\n'
    )
    env_file.chmod(0o600)
    digest = "a" * 64
    for name, value in {
        "FINDME_ENV_FILE": str(env_file),
        "VM_HOST": "192.0.2.1",
        "VM_USER": "deploy",
        "VM_SSH_KNOWN_HOSTS": "192.0.2.1 ssh-ed25519 test",
        "GHCR_USERNAME": "reader",
        "APP_IMAGE": f"ghcr.io/example/app@sha256:{digest}",
        "PHOTO_WORKER_IMAGE": f"ghcr.io/example/worker@sha256:{digest}",
        "IMPORT_WORKER_IMAGE": f"ghcr.io/example/import@sha256:{digest}",
    }.items():
        monkeypatch.setenv(name, value)
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, payload=json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    module["main"]()
    assert "registry-secret" not in repr(captured["command"])
    assert "django-secret" not in repr(captured["command"])
    assert captured["payload"]["environment"]["SECRET_KEY"] == "django-secret"
    assert "VM_SSH_KEY_FILE" not in captured["payload"]["environment"]
    assert "192.0.2.1,localhost,web" in captured["payload"]["environment"]["ALLOWED_HOSTS"]


def test_workflow_is_manual_and_build_only_by_default() -> None:
    import yaml

    workflow = yaml.load(
        (ROOT / ".github/workflows/deploy.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["deploy"]["default"] == "false"
    assert workflow["jobs"]["deploy"]["if"] == "${{ inputs.deploy }}"
    assert workflow["jobs"]["deploy"]["needs"] == ["build"]
    assert (
        "run-with-environment-secrets.py --consumer deploy"
        in workflow["jobs"]["deploy"]["steps"][-1]["run"]
    )


def test_transport_rejects_public_private_key_before_connecting(
    tmp_path: Path, monkeypatch
) -> None:
    import runpy

    import pytest

    module = runpy.run_path(str(ROOT / "deploy/run-single-vm.py"))
    key = tmp_path / "key"
    key.write_text("private key")
    key.chmod(0o644)
    env_file = tmp_path / "private.env"
    env_file.write_text(f'VM_SSH_KEY_FILE="{key}"\n')
    env_file.chmod(0o600)
    monkeypatch.setenv("VM_HOST", "192.0.2.1")
    monkeypatch.setenv("VM_USER", "deploy")
    monkeypatch.setenv("FINDME_ENV_FILE", str(env_file))
    with pytest.raises(ValueError, match="SSH key is not private"):
        module["main"]()


def test_transport_executes_remote_shell_and_preserves_dotenv_values(tmp_path: Path) -> None:
    import json
    import runpy
    import shutil

    key = tmp_path / "key"
    key.write_text("private key")
    key.chmod(0o600)
    private = tmp_path / "secrets"
    encode = runpy.run_path(str(ROOT / "scripts/run-with-environment-secrets.py"))["_encode_dotenv"]
    value = 'back\\slash "quoted" dollar$variable\nline'
    private.write_text(
        f'VM_SSH_KEY_FILE="{key}"\nGHCR_READ_TOKEN="registry-secret"\nSECRET_KEY={encode(value)}\n'
    )
    private.chmod(0o600)
    ssh = tmp_path / "ssh"
    ssh.write_text("""#!/usr/bin/env python3
import os, shlex, sys
assert sys.argv[-5:-1] == ['sudo', '-n', 'python3', '-c']
program = shlex.split(sys.argv[-1])[0]
program = program.replace("Path('/opt/photo-prjct')", "Path(os.environ['TEST_REMOTE_ROOT'])")
os.execv(sys.executable, [sys.executable, '-c', program])
""")
    ssh.chmod(0o755)
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    digest = "a" * 64
    values = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TEST_REMOTE_ROOT": str(tmp_path / "remote"),
        "FINDME_ENV_FILE": str(private),
        "VM_HOST": "192.0.2.1",
        "VM_USER": "deploy",
        "VM_SSH_KNOWN_HOSTS": "192.0.2.1 ssh-ed25519 test",
        "GHCR_USERNAME": "reader",
        "APP_IMAGE": f"ghcr.io/example/app@sha256:{digest}",
        "PHOTO_WORKER_IMAGE": f"ghcr.io/example/worker@sha256:{digest}",
        "IMPORT_WORKER_IMAGE": f"ghcr.io/example/import@sha256:{digest}",
        "PUBLIC_DOMAIN": value,
    }
    result = subprocess.run(
        ["python3", str(ROOT / "deploy/run-single-vm.py")],
        env={**os.environ, **values},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert value not in result.stdout + result.stderr
    compose = tmp_path / "check.yml"
    compose.write_text(
        "services:\n  check:\n    image: busybox\n    environment:\n      VALUE: ${SECRET_KEY}\n"
    )
    check = subprocess.run(
        [
            shutil.which("docker"),
            "compose",
            "--env-file",
            str(tmp_path / "remote/.env"),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={key: val for key, val in os.environ.items() if key != "SECRET_KEY"},
    )
    assert json.loads(check.stdout)["services"]["check"]["environment"]["VALUE"] == value.replace(
        "$", "$$"
    )
