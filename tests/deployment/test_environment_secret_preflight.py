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
MANIFEST_PATH = ROOT / "deploy/environment-secrets/staging.json"
VERIFIER_PATH = ROOT / "scripts/verify-environment-secret-projection.py"
REMOTE_HELPER_PATH = ROOT / "deploy/run-staging-remote.sh"
CONSUMERS = (
    "local-web",
    "staging-deploy",
    "staging-remote-check",
    "staging-public-monitor",
)


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_lockbox_preflight_workflow_is_isolated_from_cutover_and_exact_workflow_claims() -> None:
    workflow = _workflow()
    dispatch = workflow[True]["workflow_dispatch"]
    assert dispatch["inputs"]["preflight"] == {
        "description": "Validate all staging Lockbox projections without mutating staging",
        "required": True,
        "default": False,
        "type": "boolean",
    }

    jobs = workflow["jobs"]
    preflight = jobs["lockbox-preflight"]
    assert preflight["if"] == "${{ github.event_name == 'workflow_dispatch' && inputs.preflight }}"
    assert preflight["runs-on"] == "ubuntu-latest"
    assert preflight["environment"] == "staging"
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
                f"  --environment staging --consumer {consumer} --identity github-oidc \\",
                f"  -- python scripts/verify-environment-secret-projection.py {consumer}",
            ]
        )
        for consumer in CONSUMERS
    ]
    projection_steps = preflight["steps"][1:]
    assert [step["name"] for step in projection_steps] == [
        f"Verify Lockbox projection: {consumer}" for consumer in CONSUMERS
    ]
    assert [step["run"].strip() for step in projection_steps] == expected_runs
    serialized_preflight = json.dumps(preflight)
    assert "GITHUB_OUTPUT" not in serialized_preflight
    assert "${{ secrets." not in serialized_preflight
    assert "deploy/run-staging-remote.sh" not in serialized_preflight
    assert "actions/upload-artifact" not in serialized_preflight
    assert "appleboy/" not in serialized_preflight

    assert jobs["classify-staging-release"]["if"] == "${{ !inputs.preflight }}"
    assert jobs["build"]["if"] == (
        "${{ (github.event_name == 'push' && "
        "needs.classify-staging-release.outputs.requires_observability_bootstrap == 'false') || "
        "(github.event_name == 'workflow_dispatch' && !inputs.configure_monitoring_agent && "
        "!inputs.validate_deploy_issue && !inputs.preflight) }}"
    )
    assert jobs["deploy"]["if"] == jobs["build"]["if"]
    assert jobs["stage-observability-release"]["if"] == (
        "${{ github.event_name == 'push' && "
        "needs.classify-staging-release.outputs.requires_observability_bootstrap == 'true' && "
        "!inputs.preflight }}"
    )
    assert jobs["reconcile-staging-deploy-issue"]["if"] == (
        "${{ always() && (github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && !inputs.configure_monitoring_agent && "
        "!inputs.validate_deploy_issue && !inputs.preflight)) }}"
    )
    assert jobs["validate-staging-deploy-issue"]["if"] == (
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
        "peter-nikitin/photo-prjct/.github/workflows/promote-production.yml@refs/heads/main",
        (
            "peter-nikitin/photo-prjct/.github/workflows/"
            "staging-face-embedding-benchmark.yml@refs/heads/main"
        ),
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
        assert "deploy/run-staging-remote.sh" in serialized
        assert "${{ secrets." not in serialized
        assert "GITHUB_OUTPUT" not in serialized


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
