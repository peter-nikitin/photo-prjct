import importlib.util
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_recovery_database_is_private_and_has_no_implicit_exporter() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    assert "postgres-exporter" not in services
    assert "ports" not in services["db"]
    assert services["db"]["volumes"] == ["/var/lib/findme-photo/postgres:/var/lib/postgresql/data"]


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
