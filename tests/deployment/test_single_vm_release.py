import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_stops_old_search_owners_before_schema_and_starts_executor(tmp_path: Path) -> None:
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
            "DEPLOY_WEB": "true",
            "DEPLOY_WORKER": "false",
            "DEPLOY_IMPORT": "false",
            "DEPLOY_COMMERCE": "false",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = commands.read_text()
    assert "manage.py check --tag commerce_runtime" in calls
    assert calls.index("manage.py check --tag commerce_runtime") < calls.index("up -d --wait db")
    assert "up -d --wait db" in calls
    assert "pull db web gallery-search-worker nginx certbot-renew" in calls
    assert calls.index("stop --timeout 0 gallery-search-worker") < calls.index("stop web")
    assert calls.index("stop web") < calls.index("manage.py migrate")
    assert calls.index("manage.py migrate") < calls.index("manage.py sync_feature_flags")
    assert calls.index("manage.py sync_feature_flags") < calls.index("up -d --wait web")
    assert calls.index("up -d --wait web") < calls.index("up -d --no-deps gallery-search-worker")
    assert "--wait gallery-search-worker" not in calls
    assert "up -d --wait --force-recreate --no-deps nginx" in calls
    assert "photo-worker" not in calls
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
        "DEPLOY_WEB": "true",
        "DEPLOY_WORKER": "true",
        "DEPLOY_IMPORT": "true",
        "DEPLOY_COMMERCE": "true",
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
    assert {"192.0.2.1", "localhost", "127.0.0.1", "web"} <= set(
        captured["payload"]["environment"]["ALLOWED_HOSTS"].split(",")
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
        f'VM_SSH_KEY_FILE="{key}"\nGHCR_READ_TOKEN="registry-secret"\nPOSTGRES_EXPORTER_PASSWORD="0123456789abcdef0123456789abcdef"\nSECRET_KEY={encode(value)}\n'
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
    sync_summary = "Feature flags synchronized: created=1 updated=0 preserved=9 deleted=0."
    docker.write_text(f"""#!/bin/sh
case "$*" in
    *"manage.py sync_feature_flags")
        printf '%s\n' '{sync_summary}' '{sync_summary} unsafe-output'
        ;;
esac
""")
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
        "DEPLOY_WEB": "true",
        "DEPLOY_WORKER": "true",
        "DEPLOY_IMPORT": "true",
        "DEPLOY_COMMERCE": "true",
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
    assert sync_summary in result.stdout.splitlines()
    assert "unsafe-output" not in result.stdout + result.stderr
    values.update(
        {
            "DEPLOY_WEB": "false",
            "DEPLOY_IMPORT": "false",
            "DEPLOY_COMMERCE": "false",
            "APP_IMAGE": "",
            "IMPORT_WORKER_IMAGE": "",
            "PHOTO_WORKER_IMAGE": f"ghcr.io/example/worker@sha256:{'b' * 64}",
        }
    )
    result = subprocess.run(
        ["python3", str(ROOT / "deploy/run-single-vm.py")],
        env={**os.environ, **values},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    existing = (tmp_path / "remote/.env").read_text()
    assert f'APP_IMAGE="ghcr.io/example/app@sha256:{digest}"' in existing
    assert f'IMPORT_WORKER_IMAGE="ghcr.io/example/import@sha256:{digest}"' in existing
    assert f'PHOTO_WORKER_IMAGE="ghcr.io/example/worker@sha256:{"b" * 64}"' in existing
    (tmp_path / "remote/.env").write_text(existing.replace(f"sha256:{digest}", "latest"))
    failed = subprocess.run(
        ["python3", str(ROOT / "deploy/run-single-vm.py")],
        env={**os.environ, **values},
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    (tmp_path / "remote/.env").write_text(existing)
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


def test_certificate_issue_is_explicit_and_reloads_the_edge(tmp_path: Path) -> None:
    commands = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMANDS"\n')
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/certbot/reconcile-certificate.sh")],
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
    assert "certonly --webroot" in calls
    assert "-d findme-photo.ru -d www.findme-photo.ru" in calls
    assert "up -d --wait --force-recreate --no-deps nginx" in calls


def test_certificate_failure_does_not_restart_nginx(tmp_path: Path) -> None:
    commands = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMANDS"\nexit 1\n')
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/certbot/reconcile-certificate.sh")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "COMMANDS": str(commands),
            "DEPLOY_ROOT": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    calls = commands.read_text()
    assert "certonly --webroot" in calls
    assert "up -d" not in calls


def test_monitoring_requires_valid_secret_before_ssh(tmp_path, monkeypatch):
    import runpy

    import pytest

    module = runpy.run_path(str(ROOT / "deploy/run-single-vm.py"))
    key = tmp_path / "ssh"
    key.write_text("private")
    key.chmod(0o600)
    env_file = tmp_path / "environment"
    env_file.write_text(f'VM_SSH_KEY_FILE="{key}"\n')
    env_file.chmod(0o600)
    for name, value in {
        "VM_HOST": "192.0.2.1",
        "VM_USER": "deploy",
        "FINDME_ENV_FILE": str(env_file),
        "FINDME_DEPLOY_MODE": "monitoring",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("SSH must not run"))
    with pytest.raises(ValueError, match="invalid PostgreSQL exporter credential"):
        module["main"]()


def test_monitoring_remote_projects_secret_and_starts_only_exporter(tmp_path):
    import json
    import runpy
    import sys

    module = runpy.run_path(str(ROOT / "deploy/run-single-vm.py"))
    root = tmp_path / "root"
    root.mkdir()
    commands = tmp_path / "commands"
    for name in ("python3", "docker"):
        executable = tmp_path / name
        executable.write_text(
            '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$COMMANDS"\n'
        )
        executable.chmod(0o755)
    script = module["REMOTE_MONITORING"].replace("Path('/opt/photo-prjct')", repr(root))
    script = script.replace("root = " + repr(root), "root = Path(" + repr(str(root)) + ")")
    script = script.replace("os.fchown(secret_file.fileno(), 0, 65534)", "pass")
    secrets = root / "secrets"
    secrets.mkdir()
    (secrets / "postgres-exporter-password").write_text("B" * 32)
    password = "A" * 32
    import base64
    import io
    import tarfile

    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as bundle:
        for name in ("docker-compose.deployment.yml", "deploy/postgres-monitoring/prepare-role.py"):
            bundle.add(ROOT / name, arcname=name)
    archive = base64.b64encode(content.getvalue()).decode()
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(
            {"environment": {"POSTGRES_EXPORTER_PASSWORD": password}, "archive": archive}
        ),
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "COMMANDS": str(commands)},
    )
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout + result.stderr + commands.read_text()
    secret = root / "secrets/postgres-exporter-password"
    assert secret.read_text() == password
    assert secret.stat().st_mode & 0o777 == 0o640
    calls = commands.read_text()
    role, exporter = calls.splitlines()
    assert role.startswith("python3 ") and "prepare-role.py" in role
    assert exporter.startswith("docker compose ")
    assert "--profile observability up -d --no-deps --force-recreate postgres-exporter" in exporter
    assert " stop " not in calls


def test_worker_only_apply_updates_only_running_selected_worker(tmp_path: Path) -> None:
    commands = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMANDS"\n'
        'case "$*" in *"ps --status running -q photo-worker"*) echo running ;; esac\n'
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "COMMANDS": str(commands),
            "DEPLOY_ROOT": str(tmp_path),
            "DEPLOY_WEB": "false",
            "DEPLOY_WORKER": "true",
            "DEPLOY_IMPORT": "false",
            "DEPLOY_COMMERCE": "false",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = commands.read_text()
    assert "pull photo-worker" in calls and "up -d --no-deps photo-worker" in calls
    assert "web" not in calls and "import-worker" not in calls and "commerce-worker" not in calls


def test_monitoring_only_change_selects_exporter_without_app_images():
    import runpy

    classify = runpy.run_path(str(ROOT / "deploy/classify-release.py"))["classify"]
    selection = classify(["deploy/postgres-monitoring/prepare-role.py"])
    assert selection["exporter_changed"]
    assert not any(selection[name] for name in ("web_changed", "worker_changed", "import_changed"))


def test_gallery_executor_stops_immediately_without_lock_or_health_wait() -> None:
    import yaml

    deployment = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())
    worker = deployment["services"]["gallery-search-worker"]
    assert worker["image"] == deployment["services"]["web"]["image"]
    assert worker["entrypoint"] == [
        "python",
        "manage.py",
        "run_gallery_search_worker",
    ]
    assert "volumes" not in worker
    assert "healthcheck" not in worker
    assert worker["stop_grace_period"] == "0s"
    assert worker["environment"]["DB_HOST"] == "db"
    assert worker["cpus"] == "0.5"
    assert worker["mem_limit"] == "256m"
    assert worker["pids_limit"] == 32
    assert worker["restart"] == "unless-stopped"
    assert "profiles" not in worker
    assert not any("SECRET_ACCESS_KEY" in key or "TBANK" in key for key in worker["environment"])
