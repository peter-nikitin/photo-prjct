"""Restore commands never become an implicit part of ordinary Deploy."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    deploy_root = tmp_path / "release"
    deploy_root.mkdir()
    (deploy_root / ".env").write_text("DB_NAME=app\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$COMMAND_LOG"\n')
    docker.chmod(0o755)
    return deploy_root, fake_bin, tmp_path / "commands"


def test_restore_requires_explicit_dump_and_runs_schema_verification(tmp_path: Path):
    deploy_root, fake_bin, log = _fake_runtime(tmp_path)
    dump = tmp_path / "current.dump"
    dump.write_bytes(b"synthetic-current-schema")
    env = {
        **os.environ,
        "DEPLOY_ROOT": str(deploy_root),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
    }
    missing = subprocess.run(
        ["sh", str(ROOT / "scripts/restore-recovery-database.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert not log.exists()
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/restore-recovery-database.sh"), "--dump", str(dump)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert "up -d --wait db" in commands
    assert "pg_restore --list" in commands
    assert "pg_restore --exit-on-error" in commands
    assert "manage.py migrate --noinput" in commands
    assert "verify_pgvector_face_embeddings" in commands
    assert "verify_gallery_media_projection --all-events --require-clean" in commands
    assert " up -d web" not in commands
    assert "sha256=" in result.stdout


def test_restore_stops_at_archive_failure(tmp_path: Path):
    deploy_root, fake_bin, log = _fake_runtime(tmp_path)
    dump = tmp_path / "broken.dump"
    dump.write_bytes(b"broken")
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$COMMAND_LOG"\n'
        'case "$*" in *"pg_restore --list"*) exit 1;; esac\n'
    )
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/restore-recovery-database.sh"), "--dump", str(dump)],
        env={
            **os.environ,
            "DEPLOY_ROOT": str(deploy_root),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "pg_restore --exit-on-error" not in log.read_text()
