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
  "image inspect "*)
    image=${3:?missing image tag}
    if [ -f "$DOCKER_STATE" ] && grep -Fqx "$image" "$DOCKER_STATE"; then
      exit 0
    fi
    exit 1
    ;;
  *" build "*)
    printf '%s\\n' "${VISUAL_TEST_IMAGE:-missing}" > "$DOCKER_STATE"
    ;;
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


def _run(mode: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["sh", str(RUNNER), mode],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_runner_builds_dependency_image_once_and_reuses_it(tmp_path: Path) -> None:
    env, log = _fake_docker(tmp_path)

    _run("test", env)
    _run("test", env)

    commands = log.read_text(encoding="utf-8").splitlines()
    builds = [command for command in commands if " build " in f" {command} "]
    inspections = [command for command in commands if command.startswith("image inspect ")]
    runs = [command for command in commands if " run --rm visual-tests " in f" {command} "]

    assert len(builds) == 1
    assert len(inspections) == 2
    assert len(runs) == 2
    assert all(command.endswith("npm run test:visual:inside") for command in runs)


def test_runner_selects_snapshot_update_mode(tmp_path: Path) -> None:
    env, log = _fake_docker(tmp_path)

    _run("update", env)

    commands = log.read_text(encoding="utf-8").splitlines()
    runs = [command for command in commands if " run --rm visual-tests " in f" {command} "]
    assert runs == [
        "compose -f docker-compose.visual.yml run --rm visual-tests "
        "npm run test:visual:update:inside"
    ]
