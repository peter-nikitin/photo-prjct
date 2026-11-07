import os
import subprocess

from tests.deployment.test_deployment_scripts import ROOT


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
