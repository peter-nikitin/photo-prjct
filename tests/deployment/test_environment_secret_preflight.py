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


def test_staged_observability_source_is_bound_to_the_selected_deployment_sha(
    tmp_path: Path,
) -> None:
    _assert_staged_deployment_source_identity(tmp_path)


def test_deployment_commerce_worker_uses_the_web_app_without_photo_or_private_media_access() -> (
    None
):
    """The commerce service must stay inside its application and least-privilege boundary."""
    compose = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text(encoding="utf-8"))
    commerce_worker = compose["services"]["commerce-worker"]
    environment = commerce_worker["environment"]
    web_environment = compose["services"]["web"]["environment"]

    assert commerce_worker["image"] == "${APP_IMAGE:?APP_IMAGE must be set}"
    assert commerce_worker["entrypoint"] == ["python", "manage.py", "run_commerce_worker"]
    assert commerce_worker["command"] == []
    assert commerce_worker["profiles"] == ["commerce"]
    assert "PHOTO_PROCESSING_WORKER_TOKEN" not in environment
    assert not {key for key in environment if key.startswith(("PHOTO_WORKER_", "PRIVATE_MEDIA_"))}
    assert "COMMERCE_POSTBOX_API_KEY_ID" in environment
    assert "COMMERCE_POSTBOX_API_KEY_SECRET" in environment
    assert "COMMERCE_POSTBOX_API_KEY_ID" not in web_environment
    assert "COMMERCE_POSTBOX_API_KEY_SECRET" not in web_environment


def test_commerce_secret_inventory_records_postbox_and_order_access_ownership() -> None:
    inventory = (ROOT / "docs/runbooks/environment-secrets-inventory.md").read_text(
        encoding="utf-8"
    )

    for key, owner, trigger in (
        (
            "COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
            "Commerce maintainer",
            "Order grant signing-key rotation, suspected disclosure, or access-boundary change",
        ),
        (
            "COMMERCE_POSTBOX_API_KEY_ID",
            "Commerce email maintainer",
            "Postbox API-key rotation, service-account scope change, or suspected disclosure",
        ),
        (
            "COMMERCE_POSTBOX_API_KEY_SECRET",
            "Commerce email maintainer",
            "Postbox API-key rotation, service-account scope change, or suspected disclosure",
        ),
    ):
        assert f"| `{key}` | {owner} | {trigger} |" in inventory


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
        "  *) exit 2 ;;\nesac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
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
