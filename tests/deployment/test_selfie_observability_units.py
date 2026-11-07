from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "deploy" / "selfie-observability"


def _deployment_root_with_selector_sentinel(tmp_path: Path) -> tuple[Path, Path]:
    deploy_root = tmp_path / "deployment"
    (deploy_root / "deploy").mkdir(parents=True)
    selector_marker = tmp_path / "deployment-selector-executed"
    (deploy_root / "deploy/web-slot.py").write_text(
        f"from pathlib import Path\nPath({str(selector_marker)!r}).touch()\nprint('web-next')\n",
        encoding="utf-8",
    )
    return deploy_root, selector_marker


def _ini(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    with (OBSERVABILITY / name).open(encoding="utf-8") as source:
        parser.read_file(source)
    return parser


def test_journal_retention_is_persistent_and_bounded() -> None:
    journal = _ini("journald.conf")["Journal"]
    assert journal["Storage"] == "persistent"
    for setting in ("MaxRetentionSec", "SystemMaxUse"):
        assert journal.get(setting, "").strip().lower() not in {"", "0", "infinity"}


def test_summary_avoids_application_secrets_and_catches_up_in_project_timezone() -> None:
    service = _ini("selfie-search-summary.service")["Service"]
    timer = _ini("selfie-search-summary.timer")["Timer"]
    assert "EnvironmentFile" not in service
    assert timer.getboolean("Persistent")
    assert timer["OnCalendar"].endswith("Europe/Moscow")


def test_host_runner_uses_unambiguous_utc_window_tags_and_marks_explicit_recompute(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    journalctl = fake_bin / "journalctl"
    journalctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$JOURNAL_ARGS\"\nprintf '%s\\n' 'ordinary output'\n",
        encoding="utf-8",
    )
    journalctl.chmod(0o755)
    deploy_root, selector_marker = _deployment_root_with_selector_sentinel(tmp_path)
    result = subprocess.run(
        ["sh", OBSERVABILITY / "run-daily-summary.sh", "2026-08-03"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(deploy_root),
            "PYTHON_BIN": sys.executable,
            "JOURNAL_ARGS": str(tmp_path / "journal-args"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not selector_marker.exists()
    arguments = (tmp_path / "journal-args").read_text(encoding="utf-8").splitlines()
    assert arguments == [
        "--since",
        "2026-08-02T21:00:00Z",
        "--until",
        "2026-08-03T21:00:00Z",
        "--output=cat",
        "CONTAINER_TAG=findme.service=web",
        "+",
        "CONTAINER_TAG=findme.service=web-next",
        "+",
        "CONTAINER_TAG=findme.service=worker",
        "+",
        "CONTAINER_TAG=findme.service=nginx",
    ]
    summary = json.loads(result.stdout)
    assert summary["report_date"] == "2026-08-03"
    assert summary["recomputed"] is True


def test_host_runner_propagates_journal_failure_without_emitting_an_empty_summary(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    journalctl = fake_bin / "journalctl"
    journalctl.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    journalctl.chmod(0o755)
    deploy_root, _ = _deployment_root_with_selector_sentinel(tmp_path)

    result = subprocess.run(
        ["sh", OBSERVABILITY / "run-daily-summary.sh", "2026-08-03"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(deploy_root),
            "PYTHON_BIN": sys.executable,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 23
    assert result.stdout == ""


def test_public_log_tags_do_not_include_runtime_secrets(tmp_path) -> None:
    from tests.deployment.compose_config import render_compose

    sentinel = "private-log-sentinel"
    services = render_compose(
        tmp_path,
        "docker-compose.deployment.yml",
        "docker-compose.https.yml",
        environment={"SECRET_KEY": sentinel, "DB_PASSWORD": sentinel},
    )["services"]
    for name in ("web", "nginx"):
        logging = services[name]["logging"]
        assert logging["driver"] == "journald"
        assert logging["options"]["tag"] == f"findme.service={name}"
        assert sentinel not in json.dumps(logging)
