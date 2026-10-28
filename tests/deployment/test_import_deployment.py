"""Current one-VM import release boundaries, without the retired slot/lease gate."""

import os
import subprocess
from pathlib import Path

from tests.deployment.test_deployment_scripts import ROOT

IMPORT_IMAGE = "ghcr.io/example/import-worker:" + "a" * 40


def run_import_release(
    tmp_path: Path, env_lines: str
) -> tuple[subprocess.CompletedProcess[str], str, Path]:
    root = tmp_path / "release"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text(env_lines)
    env_file.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\n')
    docker.chmod(0o755)
    log = tmp_path / "commands"
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
            "DEPLOY_ROOT": str(root),
            "DEPLOY_IMPORT": "true",
            "IMPORT_WORKER_IMAGE": IMPORT_IMAGE,
        },
        capture_output=True,
        text=True,
    )
    return result, log.read_text() if log.exists() else "", env_file


def test_disabled_import_pulls_image_but_does_not_start_worker(tmp_path: Path) -> None:
    result, commands, env_file = run_import_release(tmp_path, "PHOTO_IMPORT_WORKER_ENABLED=False\n")
    assert result.returncode == 0, result.stderr
    assert "--profile import pull import-worker" in commands
    assert "--profile import up -d" not in commands
    assert f"IMPORT_WORKER_IMAGE={IMPORT_IMAGE}" in env_file.read_text()


def test_active_import_requires_its_private_token_before_pull(tmp_path: Path) -> None:
    original = "PHOTO_IMPORT_WORKER_ENABLED=True\nPHOTO_IMPORT_ENABLED=True\n"
    result, commands, env_file = run_import_release(tmp_path, original)
    assert result.returncode == 2
    assert "PHOTO_IMPORT_WORKER_TOKEN" in result.stderr
    assert "pull import-worker" not in commands
    assert env_file.read_text() == original


def test_active_import_starts_only_after_image_pull(tmp_path: Path) -> None:
    result, commands, env_file = run_import_release(
        tmp_path,
        "PHOTO_IMPORT_WORKER_ENABLED=True\nPHOTO_IMPORT_ENABLED=True\n"
        "PHOTO_IMPORT_WORKER_TOKEN=private-test-token\n",
    )
    assert result.returncode == 0, result.stderr
    assert commands.index("--profile import pull import-worker") < commands.index(
        "--profile import up -d --wait --no-deps import-worker"
    )
    assert "pull web" not in commands and "exec -T db" not in commands
    assert "private-test-token" not in result.stdout + result.stderr
    assert env_file.stat().st_mode & 0o777 == 0o600
