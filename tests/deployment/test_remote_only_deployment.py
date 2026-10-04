import os
import subprocess

import yaml

from tests.deployment.test_deployment_scripts import ROOT


def test_package_replacement_and_restore_keep_live_nginx_directory_inode(tmp_path):
    source = (ROOT / "deploy/run-remote.sh").read_text()
    installer = source.split("deployment_command = r'''", 1)[1].split("'''\n", 1)[0]
    function = installer.split("replace_deploy_directory() {", 1)[1].split("\n}\n", 1)[0]
    current, candidate, previous = (
        tmp_path / name for name in ("current", "candidate", "previous")
    )
    for root, version in ((current, "old"), (candidate, "new"), (previous, "old")):
        (root / "nginx").mkdir(parents=True)
        (root / "nginx/reload-nginx.sh").write_text(version)
    (current / "nginx/selected-slot").write_text("web-next\n")
    (current / "obsolete").write_text("obsolete")
    inode = (current / "nginx").stat().st_ino
    for package, version in ((candidate, "new"), (previous, "old")):
        result = subprocess.run(
            [
                "sh",
                "-eu",
                "-c",
                "replace_deploy_directory() {"
                + function
                + '\n}\nreplace_deploy_directory "$SOURCE" "$TARGET"',
            ],
            env={**os.environ, "SOURCE": str(package), "TARGET": str(current)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (current / "nginx").stat().st_ino == inode
        assert (current / "nginx/reload-nginx.sh").read_text() == version
        assert (current / "nginx/selected-slot").read_text() == "web-next\n"
        assert not (current / "obsolete").exists()


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
    assert "fleet_phase" not in source
    assert "verify-native-release.py" not in source
    assert not (ROOT / "deploy/verify-native-release.py").exists()
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "PHOTO_WORKER_REPLICAS:" not in workflow
    assert "PHOTO_WORKER_CPUS:" not in workflow
    assert "PHOTO_WORKER_MEMORY_LIMIT:" not in workflow


def test_obsolete_fleet_release_and_finalizer_are_removed():
    assert not (ROOT / "deploy/worker-pools/release.py").exists()
    assert not (ROOT / "deploy/worker-pools/finalize_initial.py").exists()


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
