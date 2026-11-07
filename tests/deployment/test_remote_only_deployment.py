import os
import subprocess

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


def test_public_web_does_not_receive_worker_authentication(tmp_path):
    from tests.deployment.compose_config import render_compose

    sentinel = "worker-auth-private-sentinel"
    services = render_compose(
        tmp_path,
        "docker-compose.deployment.yml",
        "docker-compose.https.yml",
        environment={"PHOTO_PROCESSING_WORKER_TOKEN": sentinel},
    )["services"]
    for name in ("web", "web-next", "nginx"):
        assert sentinel not in str(services[name].get("environment", {}))


def test_observability_requires_web_and_nginx_only(tmp_path):
    (tmp_path / ".env").write_text("PHOTO_PROCESSING_ENABLED=True\n")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/web-slot.py").write_text("print('web')\n")
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
