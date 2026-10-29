import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_exporter_is_pinned_private_and_reads_password_file(tmp_path):
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    exporter = services["postgres-exporter"]

    assert exporter["image"].startswith(
        "quay.io/prometheuscommunity/postgres-exporter:v0.20.1@sha256:"
    )
    assert exporter["profiles"] == ["observability"]
    assert exporter["ports"] == ["127.0.0.1:9187:9187"]
    assert exporter["environment"]["DATA_SOURCE_USER"] == "findme_monitor"
    assert (
        exporter["environment"]["DATA_SOURCE_PASS_FILE"]
        == "/run/secrets/postgres_exporter_password"
    )
    assert exporter["secrets"] == ["postgres_exporter_password"]
    assert "POSTGRES_PASSWORD" not in exporter["environment"]
    assert "DB_PASSWORD" not in exporter["environment"]
    assert "postgres-exporter" not in services["web"].get("depends_on", {})

    # Match a clean CI checkout: neither its project directory nor cwd has a .env.
    compose_file = tmp_path / "docker-compose.deployment.yml"
    compose_file.write_bytes((ROOT / "docker-compose.deployment.yml").read_bytes())
    compose_environment = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "APP_IMAGE": "example.invalid/findme/app:dev",
        "SECRET_KEY": "compose-contract-test-secret",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "example.invalid",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "compose-contract-test-password",
        "GUNICORN_WORKERS": "1",
        "GUNICORN_THREADS": "1",
        "GUNICORN_TIMEOUT": "30",
        "GUNICORN_MAX_REQUESTS": "100",
        "GUNICORN_MAX_REQUESTS_JITTER": "10",
        "GALLERY_CDN_ORIGIN": "https://images.example.invalid",
        "PUBLIC_DOMAIN": "example.invalid",
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "observability",
            "-f",
            "docker-compose.deployment.yml",
            "config",
            "--no-interpolate",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=compose_environment,
    )
    default_command = list(result.args)
    del default_command[2:4]  # Ordinary product deployment enables no monitoring profile.
    default_command.remove("--no-interpolate")
    default = json.loads(
        subprocess.run(
            default_command,
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            env=compose_environment,
        ).stdout
    )
    assert "postgres-exporter" not in default["services"]
    assert "postgres_exporter_password" not in default.get("secrets", {})
    rendered = json.loads(result.stdout)["services"]["postgres-exporter"]
    assert rendered["secrets"] == [
        {
            "source": "postgres_exporter_password",
            "target": exporter["environment"]["DATA_SOURCE_PASS_FILE"],
        }
    ]


def test_exporter_collectors_exclude_unbounded_and_sensitive_dimensions():
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    flags = set(services["postgres-exporter"]["command"])

    for disabled in (
        "stat_user_tables",
        "statio_user_tables",
        "statio_user_indexes",
        "stat_statements",
        "stat_activity",
        "replication",
        "replication_slots",
        "stat_replication",
        "stat_archiver",
        "stat_progress_vacuum",
        "roles",
        "database_wraparound",
    ):
        assert f"--no-collector.{disabled}" in flags
    assert "--collector.long_running_transactions" in flags
    assert "--collector.stat_checkpointer" not in flags  # PostgreSQL 16 uses pg_stat_bgwriter.
    assert "--no-collector.wal" not in flags
    assert "--no-collector.stat_database" not in flags


def _role_helper():
    path = ROOT / "deploy/postgres-monitoring/prepare-role.py"
    spec = importlib.util.spec_from_file_location("prepare_postgres_role", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_role_helper_rejects_insecure_secret_and_reads_only_root_owned_secret(tmp_path):
    helper = _role_helper()
    secret = tmp_path / "postgres-exporter-password"
    secret.write_text("A" * 40 + "\n")
    secret.chmod(0o640)
    metadata = secret.stat()
    safe_metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=65534, st_size=metadata.st_size
    )
    with patch.object(Path, "stat", return_value=safe_metadata):
        assert helper._password(secret) == "A" * 40
    for mode, uid, gid in ((0o644, 0, 65534), (0o640, 501, 65534), (0o640, 0, 501)):
        unsafe_metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | mode, st_uid=uid, st_gid=gid, st_size=metadata.st_size
        )
        with patch.object(Path, "stat", return_value=unsafe_metadata), pytest.raises(ValueError):
            helper._password(secret)
    secret.write_text("short\n")
    with patch.object(Path, "stat", return_value=safe_metadata), pytest.raises(ValueError):
        helper._password(secret)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(ValueError):
        helper._password(link)


def test_role_helper_sends_sql_only_on_stdin_and_sanitizes_failure(tmp_path, capsys):
    helper = _role_helper()
    password = "private'credential" + "A" * 30
    with (
        patch.object(helper, "_password", return_value=password),
        patch.object(
            helper.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr=password)
        ) as run,
    ):
        try:
            helper.prepare_role(tmp_path)
        except RuntimeError as error:
            assert password not in str(error)
        else:
            raise AssertionError("failed psql was accepted")

    args, kwargs = run.call_args
    assert password not in " ".join(args[0])
    assert "GRANT pg_monitor TO findme_monitor" in kwargs["input"]
    assert "NOSUPERUSER" in kwargs["input"]
    assert "NOREPLICATION" in kwargs["input"]
    assert "private''credential" in kwargs["input"]
    assert kwargs["capture_output"] is True
    assert capsys.readouterr().out == ""


def test_role_helper_cli_reports_only_generic_failure(tmp_path, capsys):
    helper = _role_helper()
    with (
        patch.object(
            helper, "prepare_role", side_effect=RuntimeError("private credential in psql stderr")
        ),
        patch.object(sys, "argv", ["prepare-role.py", "--deploy-root", str(tmp_path)]),
    ):
        assert helper.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "PostgreSQL monitoring role preparation failed\n"
