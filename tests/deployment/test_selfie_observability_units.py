from __future__ import annotations

import configparser
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "deploy" / "selfie-observability"


def _ini(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    with (OBSERVABILITY / name).open(encoding="utf-8") as source:
        parser.read_file(source)
    return parser


def test_journald_retention_has_the_exact_persistent_safety_caps() -> None:
    journal = _ini("journald.conf")["Journal"]

    assert dict(journal) == {
        "Storage": "persistent",
        "MaxRetentionSec": "14day",
        "SystemMaxUse": "1G",
    }


def test_summary_units_are_root_owned_and_run_at_exactly_0010_moscow() -> None:
    service = _ini("selfie-search-summary.service")
    timer = _ini("selfie-search-summary.timer")

    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["User"] == "root"
    assert service["Service"]["Group"] == "root"
    assert service["Service"]["ExecStart"] == (
        "/opt/photo-prjct/deploy/selfie-observability/run-daily-summary.sh"
    )
    assert "EnvironmentFile" not in service["Service"]
    assert timer["Timer"]["OnCalendar"] == "*-*-* 00:10:00 Europe/Moscow"
    assert timer["Timer"]["RandomizedDelaySec"] == "0"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_host_runner_uses_exact_moscow_window_tags_and_marks_explicit_recompute(
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
    result = subprocess.run(
        ["sh", OBSERVABILITY / "run-daily-summary.sh", "2026-08-03"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(ROOT),
            "DEPLOYMENT_TARGET": "staging",
            "PYTHON_BIN": str(ROOT.parents[1] / ".venv" / "bin" / "python"),
            "JOURNAL_ARGS": str(tmp_path / "journal-args"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    arguments = (tmp_path / "journal-args").read_text(encoding="utf-8").splitlines()
    assert arguments == [
        "--since",
        "2026-08-03 00:00:00 Europe/Moscow",
        "--until",
        "2026-08-04 00:00:00 Europe/Moscow",
        "--output=cat",
        "CONTAINER_TAG=findme.service=web findme.environment=staging",
        "+",
        "CONTAINER_TAG=findme.service=worker findme.environment=staging",
        "+",
        "CONTAINER_TAG=findme.service=nginx findme.environment=staging",
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

    result = subprocess.run(
        ["sh", OBSERVABILITY / "run-daily-summary.sh", "2026-08-03"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(ROOT),
            "DEPLOYMENT_TARGET": "staging",
            "PYTHON_BIN": str(ROOT.parents[1] / ".venv" / "bin" / "python"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 23
    assert result.stdout == ""


def test_public_services_use_journald_stable_nonsecret_tags_only() -> None:
    product = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    https = yaml.safe_load((ROOT / "docker-compose.https.yml").read_text(encoding="utf-8"))
    services = {
        "web": product["services"]["web"],
        "worker": product["services"]["worker"],
        "nginx": https["services"]["nginx"],
    }

    for name, service in services.items():
        assert service["logging"] == {
            "driver": "journald",
            "options": {
                "tag": (
                    f"findme.service={name} "
                    "findme.environment=${DEPLOYMENT_TARGET:?DEPLOYMENT_TARGET must be set}"
                )
            },
        }
        serialized = json.dumps(service["logging"])
        assert "TOKEN" not in serialized
        assert "SECRET" not in serialized
    assert "logging" not in product["services"]["db"]


def test_deployment_workflows_pass_the_nonsecret_target_for_compose_tags() -> None:
    staging = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    production = (ROOT / ".github" / "workflows" / "promote-production.yml").read_text(
        encoding="utf-8"
    )

    assert "DEPLOYMENT_TARGET: staging" in staging
    assert "envs: APP_IMAGE" in staging and ",DEPLOYMENT_TARGET" in staging
    assert "DEPLOYMENT_TARGET: production" in production
    assert "envs: APP_IMAGE" in production and ",DEPLOYMENT_TARGET" in production
