import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/visual/run-in-container.sh"


def _fake_docker(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$DOCKER_LOG"

case "$*" in
  "image inspect "*) exit 1 ;;
  "pull "*) exit 1 ;;
  *" build "*) printf '%s\\n' "${VISUAL_TEST_IMAGE:-missing}" > "$DOCKER_STATE" ;;
  *" run "*) exit "${DOCKER_RUN_EXIT:-0}" ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    log = tmp_path / "docker.log"
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_LOG": str(log),
            "DOCKER_STATE": str(tmp_path / "docker.state"),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )
    return env, log


def _run(mode: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(RUNNER), mode],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_runner_executes_the_selected_visual_command(tmp_path: Path) -> None:
    env, log = _fake_docker(tmp_path)

    result = _run("update", env)
    expected_command = (
        "compose -f docker-compose.visual.yml run --rm visual-tests "
        "npm run test:visual:update:inside"
    )

    assert result.returncode == 0, result.stderr
    assert expected_command in log.read_text(encoding="utf-8")


def test_runner_propagates_visual_test_failure(tmp_path: Path) -> None:
    env, _log = _fake_docker(tmp_path)
    env["DOCKER_RUN_EXIT"] = "7"

    result = _run("test", env)

    assert result.returncode == 7
