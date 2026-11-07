from __future__ import annotations

import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/deploy.yml"
VERIFIER_PATH = ROOT / "scripts/verify-environment-secret-projection.py"
CONSUMERS = (
    "local-web",
    "deploy",
    "remote-check",
    "public-monitor",
)


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_commerce_credentials_are_isolated_from_public_web(tmp_path):
    from tests.deployment.compose_config import render_compose

    credentials = {
        "COMMERCE_POSTBOX_API_KEY_ID": "mail-test-id",
        "COMMERCE_POSTBOX_API_KEY_SECRET": "mail-test-secret",
    }
    services = render_compose(
        tmp_path, "docker-compose.deployment.yml", profiles=("commerce",), environment=credentials
    )["services"]
    worker = services["commerce-worker"]["environment"]
    for name, value in credentials.items():
        assert worker[name] == value
        assert name not in services["web"]["environment"]
    assert not any(name.startswith(("PHOTO_WORKER_", "PRIVATE_MEDIA_")) for name in worker)
    assert "PHOTO_PROCESSING_WORKER_TOKEN" not in worker


def _assert_staged_deployment_source_identity(tmp_path: Path) -> None:
    workflow = _workflow()
    classify_job = workflow["jobs"]["classify-release"]
    classify = next(
        step for step in classify_job["steps"] if step.get("name") == "Classify deployment release"
    )
    stage = workflow["jobs"]["stage-observability-release"]
    bind = next(
        step for step in stage["steps"] if step.get("name") == "Bind source to staged commit"
    )

    assert classify_job["outputs"]["release_sha"] == "${{ steps.classify.outputs.release_sha }}"
    assert classify["env"] == {"DEPLOYMENT_SHA": "${{ inputs.deployment_sha }}"}
    assert stage["needs"] == ["classify-release"]
    assert (
        next(step for step in stage["steps"] if step.get("name") == "Check out staged commit")[
            "with"
        ]["ref"]
        == "${{ needs.classify-release.outputs.release_sha }}"
    )

    script = classify["run"]
    for expression, value in {
        "${{ github.sha }}": "f" * 40,
        "${{ github.event_name }}": "workflow_dispatch",
        "${{ inputs.recover_forward }}": "false",
        "${{ inputs.configure_monitoring_agent }}": "false",
        "${{ inputs.validate_deploy_issue }}": "false",
        "${{ inputs.stage_paused_observability_release }}": "false",
        "${{ inputs.verify_paused_observability_release }}": "false",
    }.items():
        script = script.replace(expression, value)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        "  cat-file) exit 0 ;;\n"
        "  rev-parse) printf '%s\\n' \"$EXPECTED_SHA\" ;;\n"
        "  diff) printf 'src/backend/config/settings.py\\n' ;;\n"
        "  *) exit 2 ;;\nesac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    (fake_bin / "python").symlink_to(sys.executable)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/classify-release.py").write_bytes(
        (ROOT / "deploy/classify-release.py").read_bytes()
    )
    output = tmp_path / "classifier-output"
    expected_sha = "a" * 40
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOYMENT_SHA": "A" * 40,
        "EXPECTED_SHA": expected_sha,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    result = subprocess.run(
        ["/bin/sh", "-c", script], cwd=tmp_path, env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert f"release_sha={expected_sha}\n" in output.read_text(encoding="utf-8")

    bind_script = bind["run"].replace(
        "${{ needs.classify-release.outputs.release_sha }}", expected_sha
    )
    exact = tmp_path / "exact"
    exact.mkdir()
    exact_result = subprocess.run(
        ["/bin/sh", "-c", bind_script],
        cwd=exact,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert exact_result.returncode == 0, exact_result.stderr
    assert (exact / "observability-release-sha").read_text(encoding="utf-8") == f"{expected_sha}\n"

    mismatch = tmp_path / "mismatch"
    mismatch.mkdir()
    mismatch_result = subprocess.run(
        ["/bin/sh", "-c", bind_script],
        cwd=mismatch,
        env={**environment, "EXPECTED_SHA": "b" * 40},
        capture_output=True,
        text=True,
    )
    assert mismatch_result.returncode != 0
    assert not (mismatch / "observability-release-sha").exists()


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_verifier_accepts_only_a_private_environment_file(consumer: str, tmp_path: Path) -> None:
    marker = f"preflight-sentinel-{uuid.uuid4().hex}"
    tmp_path.chmod(0o700)
    environment_file = tmp_path / "environment"
    environment_file.write_text(f"SECRET_KEY={marker}\n", encoding="utf-8")
    environment_file.chmod(0o600)

    completed = subprocess.run(
        [sys.executable, str(VERIFIER_PATH), consumer],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "FINDME_ENV_FILE": str(environment_file)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == f"[environment-secret-projection] consumer={consumer} status=ok\n"
    assert completed.stderr == ""
    assert marker not in " ".join(completed.args)
    assert marker not in completed.stdout + completed.stderr
    assert stat.S_IMODE(environment_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_verifier_rejects_a_nonprivate_environment_file(tmp_path: Path) -> None:
    marker = f"preflight-sentinel-{uuid.uuid4().hex}"
    tmp_path.chmod(0o700)
    environment_file = tmp_path / "environment"
    environment_file.write_text(f"SECRET_KEY={marker}\n", encoding="utf-8")
    environment_file.chmod(0o640)

    completed = subprocess.run(
        [sys.executable, str(VERIFIER_PATH), "local-web"],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "FINDME_ENV_FILE": str(environment_file)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == (
        "[environment-secret-projection] stage=boundary status=error "
        "code=environment_file_invalid\n"
    )
    assert marker not in " ".join(completed.args)
    assert marker not in completed.stdout + completed.stderr
