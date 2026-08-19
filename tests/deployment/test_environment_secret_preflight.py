from __future__ import annotations

import json
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
MANIFEST_PATH = ROOT / "deploy/environment-secrets.json"
VERIFIER_PATH = ROOT / "scripts/verify-environment-secret-projection.py"
REMOTE_HELPER_PATH = ROOT / "deploy/run-remote.sh"
CONSUMERS = (
    "local-web",
    "deploy",
    "remote-check",
    "public-monitor",
)


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_lockbox_preflight_workflow_is_isolated_from_cutover_and_exact_workflow_claims() -> None:
    workflow = _workflow()
    dispatch = workflow[True]["workflow_dispatch"]
    assert dispatch["inputs"]["preflight"] == {
        "description": "Validate all Lockbox projections without mutating the deployment",
        "required": True,
        "default": False,
        "type": "boolean",
    }

    jobs = workflow["jobs"]
    preflight = jobs["lockbox-preflight"]
    assert preflight["if"] == "${{ github.event_name == 'workflow_dispatch' && inputs.preflight }}"
    assert preflight["runs-on"] == "ubuntu-latest"
    assert "environment" not in preflight
    assert preflight["permissions"] == {"contents": "read", "id-token": "write"}
    assert "outputs" not in preflight
    assert preflight["steps"][0] == {
        "name": "Check out repository",
        "uses": "actions/checkout@v4",
        "with": {"persist-credentials": False},
    }

    expected_runs = [
        "\n".join(
            [
                "python scripts/run-with-environment-secrets.py \\",
                f"  --consumer {consumer} --identity github-oidc \\",
                f"  -- python scripts/verify-environment-secret-projection.py {consumer}",
            ]
        )
        for consumer in CONSUMERS
    ]
    projection_steps = preflight["steps"][1:5]
    assert [step["name"] for step in projection_steps] == [
        f"Verify Lockbox projection: {consumer}" for consumer in CONSUMERS
    ]
    assert [step["run"].strip() for step in projection_steps] == expected_runs
    remote_preflight = preflight["steps"][5]
    assert remote_preflight["name"] == "Verify read-only remote preflight"
    assert remote_preflight["env"] == {
        "VM_HOST": "${{ vars.VM_HOST }}",
        "VM_USER": "${{ vars.VM_USER }}",
        "VM_SSH_KNOWN_HOSTS": "${{ vars.VM_SSH_KNOWN_HOSTS }}",
    }
    assert remote_preflight["run"].strip() == "\n".join(
        [
            "python scripts/run-with-environment-secrets.py \\",
            "  --consumer remote-check --identity github-oidc \\",
            "  -- deploy/run-remote.sh remote-preflight",
        ]
    )
    serialized_preflight = json.dumps(preflight)
    assert "GITHUB_OUTPUT" not in serialized_preflight
    assert "${{ secrets." not in serialized_preflight
    assert "deploy/run-remote.sh deploy" not in serialized_preflight
    assert "actions/upload-artifact" not in serialized_preflight
    assert "appleboy/" not in serialized_preflight
    assert "${{ secrets." not in remote_preflight["run"]

    assert jobs["classify-release"]["if"] == "${{ !inputs.preflight }}"
    assert jobs["build"]["if"] == (
        "${{ !inputs.configure_monitoring_agent && !inputs.validate_deploy_issue && "
        "!inputs.preflight && !inputs.stage_paused_observability_release }}"
    )
    assert jobs["deploy"]["if"] == jobs["build"]["if"]
    assert jobs["reconcile-deploy-issue"]["if"] == (
        "${{ always() && !inputs.configure_monitoring_agent && "
        "!inputs.validate_deploy_issue && !inputs.preflight && "
        "!inputs.stage_paused_observability_release }}"
    )
    assert jobs["validate-deploy-issue"]["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.validate_deploy_issue && "
        "!inputs.preflight }}"
    )
    assert jobs["configure-monitoring-agent"]["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.configure_monitoring_agent && "
        "!inputs.validate_deploy_issue && !inputs.preflight }}"
    )

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["github_oidc"]["allowed_workflows"] == [
        "peter-nikitin/photo-prjct/.github/workflows/deploy.yml@refs/heads/main",
        "peter-nikitin/photo-prjct/.github/workflows/monitor-public-health.yml@refs/heads/main",
        "peter-nikitin/photo-prjct/.github/workflows/face-embedding-benchmark.yml@refs/heads/main",
    ]

    cutover_jobs = (
        "stage-observability-release",
        "deploy",
        "configure-monitoring-agent",
    )
    assert REMOTE_HELPER_PATH.is_file()
    for name in cutover_jobs:
        serialized = json.dumps(jobs[name])
        assert "run-with-environment-secrets.py" in serialized
        assert "deploy/run-remote.sh" in serialized
        assert "${{ secrets." not in serialized
        assert "GITHUB_OUTPUT" not in serialized

    stage = jobs["stage-observability-release"]
    assert stage["if"] == "${{ inputs.stage_paused_observability_release && !inputs.preflight }}"
    assert stage["permissions"] == {"contents": "read", "id-token": "write"}
    assert stage["needs"] == ["classify-release"]
    assert "environment" not in stage
    assert "observability-release-sha" in json.dumps(stage)
    assert "observability-source.sha256" in json.dumps(stage)
    assert "stage-paused-observability-release" in json.dumps(stage)


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
