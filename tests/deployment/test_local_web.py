from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")
DOCKER = shutil.which("docker")


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def local_launcher_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / "deploy" / "environment-secrets").mkdir(parents=True)
    command_log = tmp_path / "commands.log"
    compose_capture = tmp_path / "compose-capture"
    compose_capture.mkdir()
    resolved_environment = tmp_path / "resolved.env"
    resolved_environment.write_text(
        "SECRET_KEY=payload-secret\n"
        "MEDIA_S3_ACCESS_KEY_ID=payload-media-key\n"
        "MEDIA_S3_SECRET_ACCESS_KEY=payload-media-secret\n"
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID=payload-private-key\n"
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=payload-private-secret\n"
        "PHOTO_PROCESSING_WORKER_TOKEN=payload-worker-token\n"
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID=payload-feedback-key\n"
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY=payload-feedback-secret\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "ORIGINAL_WORKTREE_ENV=unchanged\n"
        "VM_SSH_KEY_FILE=deployment-only-ssh-sentinel\n"
        "GHCR_READ_TOKEN=deployment-only-registry-sentinel\n"
        "YANDEX_MONITORING_API_KEY=deployment-only-monitoring-sentinel\n"
        "LETSENCRYPT_EMAIL=deployment-only-email-sentinel\n",
        encoding="utf-8",
    )

    _write_executable(
        fake_bin / "git",
        r"""
case "$*" in
  *'rev-parse --is-inside-work-tree')
    [ "${GIT_VALID:-yes}" = yes ] || exit 1
    printf '%s\n' true
    ;;
  *'rev-parse --show-toplevel') printf '%s\n' "$FAKE_CHECKOUT" ;;
  *) exit 94 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        r"""
printf 'docker %s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  'context inspect --format {{json .Endpoints.docker.Host}}')
    printf '"%s"\n' "${DOCKER_CONTEXT_ENDPOINT:-unix:///var/run/docker.sock}"
    ;;
  'compose version') exit 0 ;;
  *' compose '*|compose\ *)
    index=0
    for argument in "$@"; do
      if [ "$argument" = --env-file ] || [ "$argument" = -f ]; then
        capture_next=yes
      elif [ "${capture_next:-}" = yes ]; then
        cp "$argument" "$COMPOSE_CAPTURE/$index"
        case "$argument" in
          *findme-local-web.*)
            "$REAL_PYTHON" -c \
              'import os,sys;p=sys.argv[1];print(f"{os.stat(p).st_mode&0o777:o} {p}")' \
              "$argument" >> "$MATERIAL_LOG"
            ;;
        esac
        index=$((index + 1))
        capture_next=
      fi
    done
    printf '%s\n' compose-output-sentinel
    printf '%s\n' compose-error-sentinel >&2
    if [ "${DOCKER_COMPOSE_MODE:-}" = wait ]; then
      trap ': > "$WAIT_CONTINUE"; exit 0' HUP INT TERM
      : > "$WAIT_READY"
      while [ ! -e "$WAIT_CONTINUE" ]; do /bin/sleep 0.01; done
    fi
    exit "${DOCKER_COMPOSE_EXIT:-0}"
    ;;
  *) exit 95 ;;
esac
""",
    )
    _write_executable(fake_bin / "yc", "exit 0")
    _write_executable(
        fake_bin / "mktemp",
        r"""
path=$(/usr/bin/mktemp "$@")
printf '%s\n' "$path" >> "$MKTEMP_LOG"
printf '%s\n' "$path"
""",
    )
    _write_executable(
        fake_bin / "rm",
        r"""
if [ "${RM_FAIL:-no}" = yes ]; then
  printf '%s\n' raw-rm-diagnostic-sentinel >&2
  exit 1
fi
/bin/rm "$@"
""",
    )
    _write_executable(
        root / ".venv" / "bin" / "python",
        r"""
if [ "$1" = "$FAKE_CHECKOUT/scripts/run-with-environment-secrets.py" ]; then
  printf 'resolver %s\n' "$*" >> "$COMMAND_LOG"
  if [ "${RESOLVER_EXIT:-0}" -ne 0 ]; then
    printf '%s\n' '[environment-secrets] stage=identity status=error code=identity_failed' >&2
    exit "$RESOLVER_EXIT"
  fi
  while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done
  shift
  export FINDME_ENV_FILE="$RESOLVED_ENV"
  exec "$@"
fi
if [ "$1" = - ] && [ "$#" -eq 5 ] && [ "${LOCAL_PYTHON_MODE:-}" = manifest-fail ]; then
  exit 1
fi
exec "$REAL_PYTHON" "$@"
""",
    )

    return {
        "PATH": f"{fake_bin}{os.pathsep}/bin",
        "FAKE_CHECKOUT": str(root),
        "COMMAND_LOG": str(command_log),
        "COMPOSE_CAPTURE": str(compose_capture),
        "RESOLVED_ENV": str(resolved_environment),
        "MATERIAL_LOG": str(tmp_path / "material.log"),
        "MKTEMP_LOG": str(tmp_path / "mktemp.log"),
        "WAIT_READY": str(tmp_path / "wait-ready"),
        "WAIT_CONTINUE": str(tmp_path / "wait-continue"),
        "REAL_PYTHON": sys.executable,
        "CHECKOUT": str(root),
    }


def _install_launcher(environment: dict[str, str]) -> Path:
    root = Path(environment["CHECKOUT"])
    launcher = root / "scripts" / "local-web.sh"
    launcher.write_text((ROOT / "scripts" / "local-web.sh").read_text(encoding="utf-8"))
    launcher.chmod(0o755)
    (root / "scripts" / "run-with-environment-secrets.py").write_text(
        "# resolver path is asserted by the fake Python boundary\n", encoding="utf-8"
    )
    (root / "deploy" / "environment-secrets.json").write_text(
        (ROOT / "deploy" / "environment-secrets.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "docker-compose.yml").write_text(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "Makefile").write_text(
        (ROOT / "Makefile").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return root


def _run_make(environment: dict[str, str], **extra: str) -> subprocess.CompletedProcess[str]:
    assert MAKE is not None
    root = _install_launcher(environment)
    return subprocess.run(
        [MAKE, "local-web"],
        cwd=root,
        env={**os.environ, **environment, **extra},
        text=True,
        capture_output=True,
        check=False,
    )


def _commands(environment: dict[str, str]) -> str:
    path = Path(environment["COMMAND_LOG"])
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _temporary_roots(environment: dict[str, str]) -> list[Path]:
    path = Path(environment["MKTEMP_LOG"])
    return (
        [Path(value) for value in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )


def _assert_material_removed(environment: dict[str, str]) -> None:
    roots = _temporary_roots(environment)
    assert roots
    assert all(not path.exists() for path in roots)


def test_make_staging_local_resolves_the_exact_local_web_projection_and_starts_only_db_and_web(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if the launcher requested another consumer or started a non-local service."""
    result = _run_make(local_launcher_environment)

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "[local-web] warning=local-capable-process\n[local-web] stage=launch status=ready\n"
    )
    assert result.stderr == ""
    commands = _commands(local_launcher_environment)
    assert "--consumer local-web --identity yc -- " in commands
    assert "/scripts/local-web.sh --resolved" in commands
    assert " up -d db web" in commands
    assert "worker" not in commands
    assert "clone-deployed" not in commands
    assert "ssh" not in commands
    assert "apply-deployment" not in commands
    assert "upload" not in commands
    assert "compose-output-sentinel" not in result.stdout + result.stderr
    assert "compose-error-sentinel" not in result.stdout + result.stderr
    assert (
        Path(local_launcher_environment["CHECKOUT"], ".env")
        .read_text(encoding="utf-8")
        .startswith("ORIGINAL_WORKTREE_ENV=unchanged\n")
    )
    assert _temporary_roots(local_launcher_environment)
    assert _assert_material_removed(local_launcher_environment) is None
    assert {
        line.split(" ", 1)[0]
        for line in Path(local_launcher_environment["MATERIAL_LOG"])
        .read_text(encoding="utf-8")
        .splitlines()
    } == {"600"}


def test_real_compose_merge_excludes_checkout_deployment_only_environment(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if base web.env_file survived the local Compose overlay merge."""
    assert DOCKER is not None
    result = _run_make(local_launcher_environment)
    assert result.returncode == 0, result.stderr
    captures = Path(local_launcher_environment["COMPOSE_CAPTURE"])
    shutil.copyfile(Path(local_launcher_environment["CHECKOUT"], ".env"), captures / ".env")
    rendered = subprocess.run(
        [
            DOCKER,
            "compose",
            "--env-file",
            str(captures / "0"),
            "--env-file",
            str(captures / "1"),
            "-f",
            str(captures / "2"),
            "-f",
            str(captures / "3"),
            "config",
            "--format",
            "json",
        ],
        cwd=Path(local_launcher_environment["CHECKOUT"]),
        env={"PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )

    assert rendered.returncode == 0, rendered.stderr
    environment = json.loads(rendered.stdout)["services"]["web"]["environment"]
    assert environment == {
        "SECRET_KEY": "payload-secret",
        "MEDIA_S3_ACCESS_KEY_ID": "payload-media-key",
        "MEDIA_S3_SECRET_ACCESS_KEY": "payload-media-secret",
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "payload-private-key",
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "payload-private-secret",
        "PHOTO_PROCESSING_WORKER_TOKEN": "payload-worker-token",
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "payload-feedback-key",
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "payload-feedback-secret",
        "DEBUG": "True",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,web",
        "WEB_BIND_ADDRESS": "127.0.0.1",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "app",
        "DB_HOST": "db",
        "DB_PORT": "5432",
        "VM_HOST": "",
    }
    without_reset = captures / "without-reset.yml"
    without_reset.write_text(
        (captures / "3").read_text(encoding="utf-8").replace("    env_file: !reset []\n", ""),
        encoding="utf-8",
    )
    rendered_without_reset = subprocess.run(
        [
            DOCKER,
            "compose",
            "--env-file",
            str(captures / "0"),
            "--env-file",
            str(captures / "1"),
            "-f",
            str(captures / "2"),
            "-f",
            str(without_reset),
            "config",
            "--format",
            "json",
        ],
        cwd=Path(local_launcher_environment["CHECKOUT"]),
        env={"PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )

    assert rendered_without_reset.returncode == 0, rendered_without_reset.stderr
    environment_without_reset = json.loads(rendered_without_reset.stdout)["services"]["web"][
        "environment"
    ]
    assert {
        key: environment_without_reset[key]
        for key in (
            "VM_SSH_KEY_FILE",
            "GHCR_READ_TOKEN",
            "YANDEX_MONITORING_API_KEY",
            "LETSENCRYPT_EMAIL",
        )
    } == {
        "VM_SSH_KEY_FILE": "deployment-only-ssh-sentinel",
        "GHCR_READ_TOKEN": "deployment-only-registry-sentinel",
        "YANDEX_MONITORING_API_KEY": "deployment-only-monitoring-sentinel",
        "LETSENCRYPT_EMAIL": "deployment-only-email-sentinel",
    }


def test_compose_failure_is_sanitized_and_cleans_private_material(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if Compose diagnostics escaped or a failed launch retained temporary files."""
    result = _run_make(local_launcher_environment, DOCKER_COMPOSE_EXIT="17")

    assert result.returncode != 0
    assert result.stdout == "[local-web] warning=local-capable-process\n"
    assert "[local-web] stage=launch status=error code=compose_failed" in result.stderr
    assert "compose-output-sentinel" not in result.stdout + result.stderr
    assert "compose-error-sentinel" not in result.stdout + result.stderr
    _assert_material_removed(local_launcher_environment)


def test_manifest_failure_after_temporary_creation_cleans_the_partial_materialization(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if a post-mktemp failure left the launcher's private directory behind."""
    result = _run_make(local_launcher_environment, LOCAL_PYTHON_MODE="manifest-fail")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "stage=preflight status=error code=manifest_invalid" in result.stderr
    _assert_material_removed(local_launcher_environment)
    assert " up -d db web" not in _commands(local_launcher_environment)


def test_cleanup_failure_after_success_is_nonzero_and_never_reports_readiness(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if an EXIT trap preserved success after private cleanup reported failure."""
    result = _run_make(local_launcher_environment, RM_FAIL="yes")
    retained_root = _temporary_roots(local_launcher_environment).pop().resolve()

    try:
        assert result.returncode != 0
        assert result.stdout == "[local-web] warning=local-capable-process\n"
        assert result.stderr.splitlines()[0] == (
            "[local-web] stage=cleanup status=error code=cleanup_failed "
            f"retained_path={retained_root}"
        )
        assert "stage=launch status=ready" not in result.stdout + result.stderr
        assert "raw-rm-diagnostic-sentinel" not in result.stdout + result.stderr
        assert "payload-secret" not in result.stdout + result.stderr
        assert retained_root.is_dir()
        assert {path.name: path.stat().st_mode & 0o777 for path in retained_root.iterdir()} == {
            "overrides.env": 0o600,
            "compose.yml": 0o600,
            "governed-names": 0o600,
        }
    finally:
        shutil.rmtree(retained_root, ignore_errors=True)


def _terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate(timeout=5)


def test_process_group_cleanup_terminates_a_descendant_after_its_leader_exits(
    tmp_path: Path,
) -> None:
    """Would fail if cleanup only followed the make leader and orphaned a pipe-holding child."""
    descendant_pid = tmp_path / "descendant-pid"
    terminated = tmp_path / "descendant-terminated"
    descendant_script = tmp_path / "descendant.py"
    descendant_script.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "import os",
                "import signal",
                f"pid_path = Path({str(descendant_pid)!r})",
                f"terminated_path = Path({str(terminated)!r})",
                "def stop(signum, _frame):",
                '    terminated_path.write_text(f"{signum}\\n", encoding="utf-8")',
                "    os._exit(0)",
                "signal.signal(signal.SIGTERM, stop)",
                'pid_path.write_text(str(os.getpid()), encoding="utf-8")',
                "while True:",
                "    signal.pause()",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        ["/bin/sh", "-c", '"$PYTHON_BIN" "$DESCENDANT_SCRIPT" & exit 0'],
        env={
            **os.environ,
            "PYTHON_BIN": sys.executable,
            "DESCENDANT_SCRIPT": str(descendant_script),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.wait(timeout=5) == 0
        deadline = time.monotonic() + 5
        while not descendant_pid.exists():
            if time.monotonic() >= deadline:
                pytest.fail("descendant did not reach its signal-ready checkpoint")
            time.sleep(0.02)
        assert int(descendant_pid.read_text(encoding="utf-8")) > 0
        stdout, stderr = _terminate_process_group(process)
        assert stdout == ""
        assert stderr == ""
        assert terminated.read_text(encoding="utf-8") == f"{signal.SIGTERM}\n"
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for path in (descendant_pid, terminated, descendant_script):
            path.unlink(missing_ok=True)


def _finish_signal_test_process(
    process: subprocess.Popen[str], environment: dict[str, str]
) -> tuple[str, str]:
    Path(environment["WAIT_CONTINUE"]).touch()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        return _terminate_process_group(process)


@pytest.mark.parametrize("signal_number", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_signals_clean_private_material_without_relaying_compose_output(
    local_launcher_environment: dict[str, str], signal_number: int
) -> None:
    """Would fail if an interrupt during Compose leaked local material or raw diagnostics."""
    assert MAKE is not None
    root = _install_launcher(local_launcher_environment)
    process = subprocess.Popen(
        [MAKE, "local-web"],
        cwd=root,
        env={**os.environ, **local_launcher_environment, "DOCKER_COMPOSE_MODE": "wait"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    ready = Path(local_launcher_environment["WAIT_READY"])
    deadline = time.monotonic() + 10
    try:
        while not ready.exists():
            return_code = process.poll()
            if return_code is not None:
                stdout, stderr = _terminate_process_group(process)
                pytest.fail(
                    "launcher exited before WAIT_READY "
                    f"(returncode={return_code}, stdout={stdout!r}, stderr={stderr!r})"
                )
            if time.monotonic() >= deadline:
                pytest.fail("launcher did not create WAIT_READY within 10 seconds")
            time.sleep(0.02)

        try:
            os.killpg(process.pid, signal_number)
        except ProcessLookupError:
            stdout, stderr = _terminate_process_group(process)
            pytest.fail(
                "launcher process group exited before signal delivery "
                f"(stdout={stdout!r}, stderr={stderr!r})"
            )

        temporary_roots = _temporary_roots(local_launcher_environment)
        assert temporary_roots
        cleanup_deadline = time.monotonic() + 5
        while any(path.exists() for path in temporary_roots):
            if time.monotonic() >= cleanup_deadline:
                stdout, stderr = _terminate_process_group(process)
                pytest.fail(
                    "launcher did not remove private material after signal delivery "
                    f"(stdout={stdout!r}, stderr={stderr!r})"
                )
            time.sleep(0.02)

        stdout, stderr = _finish_signal_test_process(process, local_launcher_environment)

        assert process.returncode != 0
        assert stdout == "[local-web] warning=local-capable-process\n"
        assert "compose-output-sentinel" not in stdout + stderr
        assert "compose-error-sentinel" not in stdout + stderr
        _assert_material_removed(local_launcher_environment)
    finally:
        _finish_signal_test_process(process, local_launcher_environment)


@pytest.mark.parametrize(
    ("extra", "marker"),
    [
        ({"GIT_VALID": "no"}, "stage=preflight status=error code=repository_invalid"),
        (
            {"DOCKER_CONTEXT_ENDPOINT": "ssh://operator@remote-docker.invalid/run/docker.sock"},
            "stage=preflight status=error code=docker_endpoint_invalid",
        ),
    ],
)
def test_preflight_failures_are_sanitized_before_secret_resolution(
    local_launcher_environment: dict[str, str], extra: dict[str, str], marker: str
) -> None:
    """Would fail if an invalid checkout or remote Docker endpoint reached the resolver."""
    result = _run_make(local_launcher_environment, **extra)

    assert result.returncode != 0
    assert result.stdout == ""
    assert marker in result.stderr
    assert "remote-docker.invalid" not in result.stderr
    assert "resolver " not in _commands(local_launcher_environment)


def test_missing_yc_fails_before_secret_resolution(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if the launcher deferred a missing local identity tool until after launch."""
    (Path(local_launcher_environment["PATH"].split(os.pathsep)[0]) / "yc").unlink()
    result = _run_make(local_launcher_environment)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "stage=preflight status=error code=yc_missing" in result.stderr
    assert "resolver " not in _commands(local_launcher_environment)


def test_expired_yc_failure_stays_sanitized_and_never_starts_compose(
    local_launcher_environment: dict[str, str],
) -> None:
    """Would fail if resolver authentication failure leaked input or still launched Compose."""
    result = _run_make(
        local_launcher_environment,
        RESOLVER_EXIT="2",
        SECRET_KEY="staging-secret-must-not-appear",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "[environment-secrets] stage=identity status=error code=identity_failed" in result.stderr
    assert "staging-secret-must-not-appear" not in result.stderr + _commands(
        local_launcher_environment
    )
    assert "resolver " in _commands(local_launcher_environment)
    assert " up -d db web" not in _commands(local_launcher_environment)
