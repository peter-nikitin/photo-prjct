import importlib.util
import subprocess

import pytest
import yaml

from tests.deployment.test_deployment_scripts import ROOT


@pytest.mark.parametrize("phase", [None, "verified"])
def test_normal_deployment_requires_committed_remote_marker(tmp_path, phase):
    spec = importlib.util.spec_from_file_location(
        "remote_release", ROOT / "deploy/worker-pools/release.py"
    )
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    if phase:
        import json

        (tmp_path / "worker-pools-release.json").write_text(
            json.dumps({"phase": phase, "previous": None})
        )
    with pytest.raises(ValueError, match="committed remote"):
        release.deployment_guard(tmp_path)


def test_production_compose_has_no_local_photo_workers():
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    assert not {"worker", "worker-bulk", "worker-selfie"} & services.keys()
    assert {"web", "db", "import-worker", "commerce-worker"} <= services.keys()
    assert "PHOTO_PROCESSING_WORKER_TOKEN" not in services["web"]["environment"]
    assert "nginx" in yaml.safe_load((ROOT / "docker-compose.https.yml").read_text())["services"]


def test_production_deploy_has_no_local_placement_or_restore_path():
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    assert "PHOTO_WORKER_PLACEMENT" not in source
    assert "PHOTO_PROCESSING_WORKER_TOKEN" not in source
    assert "worker-bulk" not in source
    assert "worker-selfie" not in source
    assert "fleet_phase rollback" in source
    assert "fleet_phase rollout" in source
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "PHOTO_WORKER_REPLICAS:" not in workflow
    assert "PHOTO_WORKER_CPUS:" not in workflow
    assert "PHOTO_WORKER_MEMORY_LIMIT:" not in workflow


def test_release_cannot_restore_or_start_local_photo_workers():
    source = (ROOT / "deploy/worker-pools/release.py").read_text()
    assert "def rollback_initial" not in source
    assert "def stop_local" not in source
    assert "local=True" not in source


def test_production_commands_cannot_run_legacy_compose_identity_cutover():
    assert not (ROOT / "deploy/cutover-compose-identity.sh").exists()
    assert "cutover-compose-identity" not in (ROOT / "deploy/run-remote.sh").read_text()
    assert "cutover_compose_identity" not in (ROOT / ".github/workflows/deploy.yml").read_text()


def test_observability_requires_web_and_nginx_only(tmp_path):
    (tmp_path / ".env").write_text("PHOTO_PROCESSING_ENABLED=True\n")
    calls = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *"ps -q web") echo web ;;
  *"ps -q nginx") echo nginx ;;
  inspect*web) echo 'journald|findme.service=web' ;;
  inspect*nginx) echo 'journald|findme.service=nginx' ;;
  *"exec -T web"*) : ;;
  *) exit 1 ;;
esac
""")
    docker.chmod(0o755)
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    sudo.chmod(0o755)
    import os

    result = subprocess.run(
        ["sh", str(ROOT / "deploy/verify-selfie-observability.sh")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(tmp_path),
            "COMPOSE_PROJECT_NAME": "fixture",
            "CALLS": str(calls),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "verify-probe" in calls.read_text()
    assert "worker-bulk" not in calls.read_text()
