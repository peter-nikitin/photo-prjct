import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_worker_release_b_stays_queued_when_docs_only_c_arrives_during_deployment_a():
    classify = module("classify-release").classify
    assert classify(["src/worker/photo_worker/client.py"])["worker_changed"]
    assert not any(classify(["docs/runbooks/deployment.md"]).values())
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    # GitHub's default one-slot queue replaces B with C even without canceling A.
    assert workflow["concurrency"] == {
        "group": "deploy",
        "cancel-in-progress": False,
        "queue": "max",
    }


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_forward_recovery_dispatch_is_explicit_exact_sha_and_web_only():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    event = workflow.get("on", workflow.get(True))
    assert event["workflow_dispatch"]["inputs"]["recover_forward"]["default"] is False
    run = next(s for s in workflow["jobs"]["deploy"]["steps"] if s.get("name") == "Run deployment")
    assert run["env"]["RECOVER_FORWARD"] == "${{ inputs.recover_forward && 'True' || 'False' }}"
    assert run["env"]["RELEASE_SHA"] == "${{ needs.classify-release.outputs.release_sha }}"


@pytest.mark.parametrize("special", [None, "preflight", "configure_monitoring_agent"])
def test_forward_dispatch_classification_forces_web_only_and_rejects_other_modes(tmp_path, special):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    step = next(
        s for s in workflow["jobs"]["classify-release"]["steps"] if s.get("id") == "classify"
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expressions = {
        "github.sha": sha,
        "github.event_name": "workflow_dispatch",
        "inputs.recover_forward": "true",
    }
    if special:
        expressions[f"inputs.{special}"] = "true"
    program = re.sub(
        r"\$\{\{\s*(.*?)\s*\}\}", lambda m: expressions.get(m[1], "false"), step["run"]
    )
    output = tmp_path / "output"
    result = subprocess.run(
        ["sh", "-c", program],
        cwd=ROOT,
        env={**os.environ, "DEPLOYMENT_SHA": sha, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )
    if special:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert values["web_changed"] == "true"
        assert values["worker_changed"] == values["worker_base_changed"] == "false"
        assert values["release_sha"] == sha


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/runbooks/deployment.md", "README.md"], (False, False, False)),
        (["src/backend/processing/views.py"], (True, False, False)),
        (["src/import_worker/import_worker/runtime.py"], (True, False, False)),
        (["src/worker/photo_worker/client.py"], (False, True, False)),
        (["src/worker/requirements.cpu.txt"], (False, True, True)),
        (["Dockerfile.worker-base"], (False, True, True)),
        (["Dockerfile.worker"], (False, True, False)),
        ([".dockerignore"], (True, True, True)),
        (
            ["src/backend/processing/views.py", "src/worker/photo_worker/client.py"],
            (True, True, False),
        ),
        (["deploy/apply-deployment.sh"], (True, False, False)),
        ([".github/workflows/deploy.yml"], (True, True, True)),
        (["tests/deployment/test_component_release.py"], (False, False, False)),
        (["scripts/run-with-environment-secrets.py"], (True, False, False)),
        (["src/worker/tests/test_runtime.py"], (False, False, False)),
    ],
)
def test_only_effective_component_inputs_are_published(paths, expected):
    selected = module("classify-release").classify(paths)
    assert tuple(selected.values()) == expected


def test_workflow_builds_worker_with_immutable_base_and_latest_only_after_smoke():
    jobs = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())["jobs"]
    build = jobs["build"]
    assert "web_changed == 'true'" in jobs["deploy"]["if"]
    assert "web_changed == 'true'" in build["if"]
    assert "worker_changed == 'true'" in build["if"]
    steps = {step.get("name"): step for step in build["steps"]}
    base = steps["Build and push worker base"]
    assert "worker_base_changed == 'true'" in base["if"]
    worker = steps["Build and push worker image"]
    assert "WORKER_BASE_IMAGE=${{ steps.base_ref.outputs.image }}" in worker["with"]["build-args"]
    assert "${{ steps.image.outputs.worker_image }}" in worker["with"]["tags"]
    assert "latest" not in worker["with"]["tags"]
    publish = steps["Publish current worker pointer"]
    assert "imagetools create" in publish["run"]
    assert "steps.worker.outputs.digest" in publish["env"]["WORKER_DIGEST"]
    assert "continue-on-error" not in publish
    assert "worker_changed == 'true'" in publish["if"]
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    remote = (ROOT / "deploy/run-remote.sh").read_text()
    for retired in (
        "fleet_phase",
        "WORKER_POOL_RELEASE",
        "worker-pools-current.json",
        "worker-pools/release.py",
    ):
        assert retired not in source + remote
    assert "WORKER_IMAGE" not in jobs["deploy"]["steps"][-1]["env"]


def test_failed_worker_pointer_publication_reports_failure(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["build"]["steps"]
        if step.get("name") == "Publish current worker pointer"
    )
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\nexit 1\n')
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "WORKER_DIGEST": "sha256:" + "a" * 64,
            "GITHUB_REPOSITORY": "example/photo-prjct",
            "DOCKER_LOG": str(tmp_path / "calls"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert (
        (tmp_path / "calls").read_text().strip()
        == "buildx imagetools create --tag ghcr.io/example/photo-prjct-worker:latest "
        "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64
    )


def test_native_collector_helper_installs_verified_owned_source_and_can_remove(tmp_path):
    package = tmp_path / "package"
    candidate = tmp_path / "candidate"
    package.mkdir()
    candidate.mkdir()
    for name in ("metrics.py", "metrics.service", "metrics.timer"):
        shutil.copy2(ROOT / "deploy/worker-pools" / name, package / name)
        shutil.copy2(package / name, candidate / name)
    binary = tmp_path / "bin"
    binary.mkdir()
    systemctl = binary / "systemctl"
    systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FINDME_SYSTEMCTL_LOG"\n')
    systemctl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "FINDME_METRICS_TEST_ROOT": str(tmp_path),
        "FINDME_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
    }
    helper = ROOT / "deploy/worker-pools/metrics-root-helper.sh"
    result = subprocess.run(["sh", helper, "install"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runtime/metrics.py").read_bytes() == (package / "metrics.py").read_bytes()
    assert (tmp_path / "config/metrics.json").is_file()
    assert (
        "enable --now findme-worker-pool-metrics.timer" in (tmp_path / "systemctl.log").read_text()
    )
    (candidate / "metrics.py").write_text("unreviewed source")
    result = subprocess.run(["sh", helper, "install"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert (tmp_path / "runtime/metrics.py").read_bytes() == (package / "metrics.py").read_bytes()
    result = subprocess.run(["sh", helper, "remove"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "runtime/metrics.py").exists()
