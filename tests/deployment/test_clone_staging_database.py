import hashlib
import json
import os
import pty
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/clone-staging-db.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run(*, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def _run_interactive(*, env: dict[str, str], reply: str) -> tuple[int, str]:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={**os.environ, **env},
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
    )
    os.close(slave)
    os.write(master, reply.encode("utf-8"))
    try:
        stdout, stderr = process.communicate(timeout=5)
    finally:
        os.close(master)
    return process.returncode, stdout + stderr


@pytest.fixture
def clone_env(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    mktemp_log = tmp_path / "mktemp.log"
    config = tmp_path / "compose.json"
    config.write_text(
        """{
  "name": "clone-guard-test",
  "services": {
    "db": {
      "image": "postgres:16.6@sha256:deadbeef",
      "environment": {"POSTGRES_DB": "local_app", "POSTGRES_USER": "local_user"}
    },
    "web": {"environment": {"DB_HOST": "db"}}
  }
}
""",
        encoding="utf-8",
    )
    _write_executable(
        fake_bin / "ssh",
        r"""
printf 'ssh %s\n' "$*" >> "$COMMAND_LOG"
if [ "${SSH_PAUSE_MODE:-}" = version ]; then
  case "$*" in
    *'postgres --version'*)
      : > "$SSH_PAUSE_FILE"
      while [ ! -e "$SSH_CONTINUE_FILE" ]; do
        sleep 0.01
      done
      ;;
  esac
fi
case "$*" in
  *'pg_dump '*)
    case "${SSH_DUMP_MODE:-success}" in
      success) printf 'PGDMP\001validated staging dump' ;;
      interrupted) printf 'PGDMP\001partial dump'; exit 23 ;;
      empty) exit 0 ;;
      truncated) printf 'not a PostgreSQL custom dump' ;;
    esac
    ;;
  *'postgres --version'*)
    printf 'postgres (PostgreSQL) %s\n' "${REMOTE_POSTGRES_VERSION:-16.4}"
    ;;
  *'printenv POSTGRES_DB'*)
    printf '%s\n' "${REMOTE_DATABASE:-staging_app}"
    ;;
  *'printenv POSTGRES_USER'*)
    printf '%s\n' "${REMOTE_USER:-staging_user}"
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        """
printf 'docker %s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  'context show') printf '%s\\n' "${DOCKER_CURRENT_CONTEXT:-default}" ;;
  'context inspect --format {{json .Endpoints.docker.Host}} '*)
    printf '"%s"\\n' "${DOCKER_CONTEXT_ENDPOINT:-unix:///var/run/docker.sock}"
    ;;
  'context inspect --format {{json .Endpoints.docker.Host}}')
    printf '"%s"\\n' "${DOCKER_CONTEXT_ENDPOINT:-unix:///var/run/docker.sock}"
    ;;
  'compose version') exit 0 ;;
  'compose config --format json') cat "$COMPOSE_CONFIG" ;;
  *'postgres --version'*) printf 'postgres (PostgreSQL) %s\\n' "${LOCAL_POSTGRES_VERSION:-16.6}" ;;
  *'pg_restore --list /dump'*)
    case "$*" in
      *'.local-safety.dump:/dump:ro'*)
        if [ "${LOCAL_SAFETY_VALIDATION_MODE:-success}" = fail ]; then
          echo 'invalid local safety archive' >&2
          exit 1
        fi
        ;;
    esac
    if [ "${PG_RESTORE_MODE:-success}" = fail ]; then
      echo 'invalid archive' >&2
      exit 1
    fi
    printf '1; 1259 1 TABLE public events app\\n2; 1259 2 TABLE public photos app\\n'
    ;;
  *'compose up -d db'*) exit 0 ;;
  'compose ps --status running --services web')
    if [ "${WEB_RUNNING:-yes}" = yes ]; then
      printf 'web\\n'
    fi
    ;;
  'compose stop web')
    if [ "${WEB_STOP_MODE:-success}" = fail ]; then
      echo 'could not stop web' >&2
      exit 1
    fi
    ;;
  *'pg_isready '*)
    printf 'postgres:5432 - accepting connections\\n'
    ;;
  *'pg_dump --format=custom '*)
    if [ "${LOCAL_SAFETY_DUMP_MODE:-success}" = fail ]; then
      echo 'could not create local safety dump' >&2
      exit 1
    fi
    printf 'PGDMP\\001local safety dump'
    ;;
  *'pg_restore --exit-on-error '*)
    count=0
    if [ -f "$PG_RESTORE_COUNT_FILE" ]; then
      IFS= read -r count < "$PG_RESTORE_COUNT_FILE"
    fi
    count=$((count + 1))
    printf '%s\\n' "$count" > "$PG_RESTORE_COUNT_FILE"
    cat > "$PG_RESTORE_INPUT_PREFIX.$count"
    case "${LOCAL_RESTORE_MODE:-success}:$count" in
      staging-fail:1|all-fail:*)
        echo 'restore failed' >&2
        exit 1
        ;;
    esac
    ;;
  *'compose run --rm --no-deps --entrypoint python web manage.py shell -c '*)
    if [ "${DJANGO_PAUSE_MODE:-}" = shell ]; then
      : > "$DJANGO_PAUSE_FILE"
      while [ ! -e "$DJANGO_CONTINUE_FILE" ]; do
        sleep 0.01
      done
    fi
    case "${DJANGO_DATABASE_STATE:-ready}" in
      ready) exit 0 ;;
      no-applied-migrations) exit 10 ;;
      migrations-ahead-of-checkout) exit 11 ;;
      invalid-migration-table) exit 12 ;;
      connectivity-failure) exit 13 ;;
    esac
    ;;
  *'compose run --rm --no-deps --entrypoint python web manage.py showmigrations --plan'*)
    case "${DJANGO_DATABASE_STATE:-ready}" in
      pending-migrations) printf '[ ] 0002_pending\n' ;;
      showmigrations-failure) exit 1 ;;
      *) printf '[X] 0001_initial\n' ;;
    esac
    ;;
  *'compose run --rm --no-deps --entrypoint python web manage.py makemigrations --check --dry-run'*)
    case "${DJANGO_DATABASE_STATE:-ready}" in
      model-drift|makemigrations-failure) exit 1 ;;
    esac
    ;;
  *'psql '*)
    count=0
    if [ -f "$PSQL_COUNT_FILE" ]; then
      IFS= read -r count < "$PSQL_COUNT_FILE"
    fi
    count=$((count + 1))
    printf '%s\\n' "$count" > "$PSQL_COUNT_FILE"
    case ",${PSQL_FAIL_AT:-}," in
      *",$count,"*)
        echo 'injected psql failure' >&2
        exit 1
        ;;
    esac
    if [ "${PSQL_PAUSE_AT:-0}" = "$count" ]; then
      : > "$PSQL_PAUSE_FILE"
      while [ ! -e "$PSQL_CONTINUE_FILE" ]; do
        sleep 0.01
      done
    fi
    exit 0
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "df",
        """
if [ "${DF_MODE:-enough}" = insufficient ]; then
  printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
  printf '%s\\n' '/dev/fake 1024 1024 0 100% /'
else
  printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
  printf '%s\\n' '/dev/fake 102400 100 102300 1% /'
fi
""",
    )
    _write_executable(
        fake_bin / "mktemp",
        """
printf 'mktemp %s\\n' "$*" >> "$MKTEMP_LOG"
exec "$REAL_MKTEMP" "$@"
""",
    )
    _write_executable(
        fake_bin / "mv",
        """
count=0
if [ -f "$MV_COUNT_FILE" ]; then
  IFS= read -r count < "$MV_COUNT_FILE"
fi
count=$((count + 1))
printf '%s\\n' "$count" > "$MV_COUNT_FILE"
if [ "${MV_FAIL_AT:-0}" = "$count" ]; then
  exit 1
fi
if [ "${MV_PAUSE_AT:-0}" = "$count" ]; then
  : > "$MV_PAUSE_FILE"
  while [ ! -e "$MV_CONTINUE_FILE" ]; do
    sleep 0.01
  done
fi
exec "$REAL_MV" "$@"
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "MKTEMP_LOG": str(mktemp_log),
        "REAL_MKTEMP": str(shutil.which("mktemp")),
        "MV_COUNT_FILE": str(tmp_path / "mv-count"),
        "REAL_MV": str(shutil.which("mv")),
        "MV_PAUSE_FILE": str(tmp_path / "mv-paused"),
        "MV_CONTINUE_FILE": str(tmp_path / "mv-continue"),
        "PG_RESTORE_COUNT_FILE": str(tmp_path / "pg-restore-count"),
        "PG_RESTORE_INPUT_PREFIX": str(tmp_path / "pg-restore-input"),
        "PSQL_COUNT_FILE": str(tmp_path / "psql-count"),
        "PSQL_PAUSE_FILE": str(tmp_path / "psql-paused"),
        "PSQL_CONTINUE_FILE": str(tmp_path / "psql-continue"),
        "DJANGO_PAUSE_FILE": str(tmp_path / "django-paused"),
        "DJANGO_CONTINUE_FILE": str(tmp_path / "django-continue"),
        "SSH_PAUSE_FILE": str(tmp_path / "ssh-paused"),
        "SSH_CONTINUE_FILE": str(tmp_path / "ssh-continue"),
        "COMPOSE_CONFIG": str(config),
        "DOCKER_CONTEXT": "clone-test",
        "DOCKER_CONTEXT_ENDPOINT": "unix:///var/run/docker.sock",
        "STAGING_SSH_TARGET": "developer@staging.example",
    }


def _commands(env: dict[str, str]) -> str:
    command_log = Path(env["COMMAND_LOG"])
    return command_log.read_text(encoding="utf-8") if command_log.exists() else ""


def _assert_no_remote_or_local_replacement(env: dict[str, str]) -> None:
    commands = _commands(env)
    assert "ssh " not in commands
    assert " compose exec " not in f" {commands} "
    assert " psql " not in f" {commands} "


def _assert_local_database_was_not_touched(env: dict[str, str]) -> None:
    commands = _commands(env)
    assert "\ndocker compose exec " not in commands
    assert "\ndocker compose run " not in commands
    assert "\ndocker compose up " not in commands
    assert " psql " not in f" {commands} "
    assert " down " not in f" {commands} "
    assert "--volumes" not in commands
    assert "pgdata" not in commands


def _web_validation_lines(env: dict[str, str]) -> list[str]:
    return [
        line
        for line in _commands(env).splitlines()
        if line.startswith("docker compose run ") and " web manage.py " in line
    ]


def _published_backup_artifacts(backup_dir: Path) -> list[Path]:
    if not backup_dir.exists():
        return []
    return [path for path in backup_dir.iterdir() if path.name != ".locks"]


def _write_existing_staging_dump(tmp_path: Path) -> Path:
    dump_path = tmp_path / "retained-staging.dump"
    dump_path.write_bytes(b"PGDMP\x01retained staging dump")
    digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    dump_path.with_suffix(".dump.sha256").write_text(
        f"{digest}  {dump_path.name}\n", encoding="utf-8"
    )
    return dump_path


def _replace_checksum_with_other_file(dump_path: Path) -> None:
    other_dump_path = dump_path.with_name("other-retained.dump")
    other_dump_path.write_bytes(b"PGDMP\x01different retained dump")
    other_digest = hashlib.sha256(other_dump_path.read_bytes()).hexdigest()
    dump_path.with_suffix(".dump.sha256").write_text(
        f"{other_digest}  {other_dump_path.name}\n", encoding="utf-8"
    )


def _append_another_checksum_entry(dump_path: Path) -> None:
    other_dump_path = dump_path.with_name("extra-retained.dump")
    other_dump_path.write_bytes(b"PGDMP\x01extra retained dump")
    other_digest = hashlib.sha256(other_dump_path.read_bytes()).hexdigest()
    checksum_path = dump_path.with_suffix(".dump.sha256")
    checksum_path.write_text(
        checksum_path.read_text(encoding="utf-8") + f"{other_digest}  {other_dump_path.name}\n",
        encoding="utf-8",
    )


def _write_malformed_checksum(dump_path: Path) -> None:
    dump_path.with_suffix(".dump.sha256").write_text(
        f"{'A' * 64}  {dump_path.name}\n", encoding="utf-8"
    )


def test_guard_requires_staging_ssh_target_before_any_remote_or_local_action(
    clone_env: dict[str, str],
) -> None:
    env = {key: value for key, value in clone_env.items() if key != "STAGING_SSH_TARGET"}

    result = _run(env=env)

    assert result.returncode != 0
    assert "STAGING_SSH_TARGET" in result.stderr
    _assert_no_remote_or_local_replacement(env)


def test_guard_stops_when_interactive_confirmation_is_declined(
    clone_env: dict[str, str],
) -> None:
    returncode, output = _run_interactive(env=clone_env, reply="no\n")

    assert returncode != 0
    assert "clone-guard-test" in output
    assert "local_app" in output
    assert "Confirmation declined" in output
    _assert_no_remote_or_local_replacement(clone_env)


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("config", "expected_message"),
    [
        (
            '{"name": "clone-guard-test", "services": {"web": {"environment": {"DB_HOST": "db"}}}}',
            "db service",
        ),
        (
            """{
  "name": "clone-guard-test",
  "services": {
    "db": {
      "image": "postgres:16.6@sha256:deadbeef",
      "environment": {"POSTGRES_DB": "local_app", "POSTGRES_USER": "local_user"}
    },
    "web": {"environment": {"DB_HOST": "staging-db.example"}}
  }
}""",
            "DB_HOST",
        ),
    ],
)
def test_guard_rejects_unsafe_rendered_local_database_configuration(
    clone_env: dict[str, str], config: str, expected_message: str
) -> None:
    config_path = Path(clone_env["COMPOSE_CONFIG"])
    config_path.write_text(config, encoding="utf-8")

    result = _run(env=clone_env)

    assert result.returncode != 0
    assert expected_message in result.stderr
    _assert_no_remote_or_local_replacement(clone_env)


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("name", "clone-guard\x1b[2Jtest"),
        ("POSTGRES_DB", "local\x01app"),
        ("POSTGRES_USER", "local-user\x7f"),
    ],
)
def test_guard_rejects_terminal_control_values_before_any_remote_or_local_action(
    clone_env: dict[str, str], field: str, unsafe_value: str
) -> None:
    config = {
        "name": "clone-guard-test",
        "services": {
            "db": {
                "image": "postgres:16.6@sha256:deadbeef",
                "environment": {"POSTGRES_DB": "local_app", "POSTGRES_USER": "local_user"},
            },
            "web": {"environment": {"DB_HOST": "db"}},
        },
    }
    if field == "name":
        config["name"] = unsafe_value
    else:
        config["services"]["db"]["environment"][field] = unsafe_value
    Path(clone_env["COMPOSE_CONFIG"]).write_text(json.dumps(config), encoding="utf-8")

    result = _run(env=clone_env)

    assert result.returncode != 0
    assert "control characters" in result.stderr
    _assert_no_remote_or_local_replacement(clone_env)


def test_guard_requires_exact_noninteractive_confirmation_before_any_remote_or_local_action(
    clone_env: dict[str, str],
) -> None:
    result = _run(env={**clone_env, "CONFIRM_REPLACE_LOCAL_DB": "Yes"})

    assert result.returncode != 0
    assert "CONFIRM_REPLACE_LOCAL_DB=yes" in result.stderr
    _assert_no_remote_or_local_replacement(clone_env)


@pytest.mark.parametrize(
    ("environment", "unsafe_endpoint"),
    [
        (
            {
                "DOCKER_CONTEXT": "remote-ssh",
                "DOCKER_CONTEXT_ENDPOINT": "ssh://operator@remote-docker.example/run/docker.sock",
            },
            "remote-docker.example",
        ),
        (
            {
                "DOCKER_CONTEXT": "",
                "DOCKER_HOST": "tcp://192.0.2.50:2376",
            },
            "192.0.2.50",
        ),
        (
            {
                "DOCKER_CONTEXT": "",
                "DOCKER_HOST": "http://192.0.2.51:2375",
            },
            "192.0.2.51",
        ),
        (
            {
                "DOCKER_CONTEXT": "",
                "DOCKER_HOST": "https://198.51.100.20:2376",
            },
            "198.51.100.20",
        ),
        (
            {
                "DOCKER_CONTEXT": "",
                "DOCKER_HOST": "npipe:////./pipe/docker_engine",
            },
            "docker_engine",
        ),
    ],
    ids=(
        "ssh-context",
        "non-loopback-docker-host",
        "non-loopback-http",
        "non-loopback-https",
        "unknown-scheme",
    ),
)
def test_guard_rejects_remote_docker_endpoint_before_confirmation_dump_or_database_action(
    clone_env: dict[str, str],
    environment: dict[str, str],
    unsafe_endpoint: str,
) -> None:
    """Would fail if Docker commands could target an SSH or non-loopback daemon."""
    result = _run(
        env={
            **clone_env,
            **environment,
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode != 0
    assert "local Docker endpoint" in result.stderr
    assert unsafe_endpoint not in result.stderr
    commands = _commands(clone_env)
    assert "docker context inspect" in commands
    assert "ssh " not in commands
    assert "docker compose config" not in commands
    assert " psql " not in f" {commands} "


@pytest.mark.parametrize(
    "environment",
    [
        pytest.param(
            {
                "DOCKER_CONTEXT": "desktop-linux",
                "DOCKER_CONTEXT_ENDPOINT": "unix:///Users/developer/.docker/run/docker.sock",
            }
        ),
        pytest.param(
            {"DOCKER_CONTEXT": "", "DOCKER_HOST": "tcp://127.25.0.1:2375"},
            marks=pytest.mark.clone_staging_slow,
        ),
        pytest.param(
            {"DOCKER_CONTEXT": "", "DOCKER_HOST": "tcp://[::1]:2375"},
            marks=pytest.mark.clone_staging_slow,
        ),
        pytest.param(
            {"DOCKER_CONTEXT": "", "DOCKER_HOST": "tcp://localhost:2375"},
            marks=pytest.mark.clone_staging_slow,
        ),
    ],
    ids=("docker-desktop-unix", "ipv4-loopback", "ipv6-loopback", "localhost"),
)
def test_guard_accepts_local_unix_and_loopback_docker_endpoints(
    clone_env: dict[str, str],
    tmp_path: Path,
    environment: dict[str, str],
) -> None:
    """Would fail if a safe Docker Desktop or loopback endpoint bypassed endpoint inspection."""
    result = _run(
        env={
            **clone_env,
            **environment,
            "BACKUP_DIR": str(tmp_path / "backups"),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode == 0, result.stderr
    assert "docker context inspect" in _commands(clone_env)


def test_dump_stream_uses_remote_container_and_publishes_validated_artifacts(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "REMOTE_DATABASE": "staging app; not-a-command",
            "REMOTE_USER": "staging user",
        }
    )

    assert result.returncode == 0, result.stderr
    dump_paths = [
        path for path in backup_dir.glob("*.dump") if not path.name.endswith(".local-safety.dump")
    ]
    assert len(dump_paths) == 1
    dump_path = dump_paths[0]
    assert dump_path.read_bytes() == b"PGDMP\x01validated staging dump"
    assert f"mktemp {dump_path}.XXXXXX" in Path(clone_env["MKTEMP_LOG"]).read_text(encoding="utf-8")
    checksum_path = backup_dir / f"{dump_path.name}.sha256"
    metadata_path = backup_dir / f"{dump_path.stem}.metadata"
    assert checksum_path.exists()
    assert metadata_path.exists()
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (dump_path, checksum_path, metadata_path)
    )
    metadata = metadata_path.read_text(encoding="utf-8")
    assert "staging_host=staging.example" in metadata
    assert "postgresql_major=16" in metadata
    assert "database=staging app; not-a-command" in metadata
    assert "dump_toc_entries=2" in metadata

    commands = _commands(clone_env)
    assert (
        "docker compose --project-name photo-prjct-staging "
        "-f /opt/photo-prjct/docker-compose.prod.yml exec -T db postgres --version"
    ) in commands
    assert (
        "docker run --rm --network none postgres:16.6@sha256:deadbeef postgres --version"
    ) in commands
    assert (
        "pg_dump --format=custom --no-owner --no-acl "
        "--username='staging user' --dbname='staging app; not-a-command'"
    ) in commands
    assert "pg_restore --list /dump" in commands
    assert "docker run --rm --network none --volume" in commands
    assert "password" not in (commands + result.stdout + result.stderr).lower()
    assert "docker compose up -d db" in commands
    assert " down " not in f" {commands} "
    assert "--volumes" not in commands
    assert "pgdata" not in commands
    validation_lines = _web_validation_lines(clone_env)
    assert any("manage.py showmigrations --plan" in line for line in validation_lines)
    assert any("manage.py makemigrations --check --dry-run" in line for line in validation_lines)
    assert all(
        "docker compose exec -T db" in line
        for line in commands.splitlines()
        if line.startswith("docker compose exec ")
    )
    verification = subprocess.run(
        ["shasum", "-a", "256", "-c", checksum_path.name],
        cwd=backup_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    assert verification.returncode == 0, verification.stderr


def test_restore_replaces_only_the_resolved_local_database_after_a_safety_dump(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if replacement starts another service or targets any database but local_app."""
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode == 0, result.stderr
    assert "Local database replacement completed" in result.stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2

    commands = _commands(clone_env)
    lines = commands.splitlines()
    assert "docker compose up -d db" in lines
    assert not any(
        line.startswith("docker compose up ") and line != "docker compose up -d db"
        for line in lines
    )
    assert any(
        "docker compose exec -T db pg_isready --username=local_user --dbname=postgres" in line
        for line in lines
    )
    safety_dump_index = next(
        index
        for index, line in enumerate(lines)
        if "docker compose exec -T db pg_dump --format=custom" in line
        and "--dbname=local_app" in line
    )
    terminate_index = next(
        index
        for index, line in enumerate(lines)
        if "docker compose exec -T db psql" in line and "pg_terminate_backend" in line
    )
    drop_index = next(
        index
        for index, line in enumerate(lines)
        if "docker compose exec -T db psql" in line and "DROP DATABASE IF EXISTS" in line
    )
    create_index = next(
        index
        for index, line in enumerate(lines)
        if "docker compose exec -T db psql" in line and "CREATE DATABASE" in line
    )
    restore_index = next(
        index
        for index, line in enumerate(lines)
        if (
            "docker compose exec -T db pg_restore "
            "--exit-on-error --clean --if-exists --no-owner --no-acl"
        )
        in line
    )
    assert safety_dump_index < terminate_index < drop_index < create_index < restore_index
    assert "--username=local_user --dbname=postgres" in lines[terminate_index]
    assert "--username=local_user --dbname=postgres" in lines[drop_index]
    assert "--username=local_user --dbname=postgres" in lines[create_index]
    assert 'DROP DATABASE IF EXISTS "local_app"' in lines[drop_index]
    assert 'CREATE DATABASE "local_app" OWNER "local_user"' in lines[create_index]
    assert "--username=local_user --dbname=local_app" in lines[restore_index]
    assert "--host" not in commands
    assert " -h " not in f" {commands} "
    assert " down " not in f" {commands} "
    assert "--volumes" not in commands
    assert "pgdata" not in commands
    assert len(_web_validation_lines(clone_env)) == 3
    assert all(
        "docker compose exec -T db" in line
        for line in lines
        if line.startswith("docker compose exec ")
    )


def test_restore_stops_running_web_before_safety_dump_and_leaves_it_stopped(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if the normal web service could write during replacement or were restarted."""
    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(tmp_path / "backups"),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "WEB_RUNNING": "yes",
        }
    )

    assert result.returncode == 0, result.stderr
    lines = _commands(clone_env).splitlines()
    ps_index = lines.index("docker compose ps --status running --services web")
    stop_index = lines.index("docker compose stop web")
    safety_index = next(
        index
        for index, line in enumerate(lines)
        if "docker compose exec -T db pg_dump --format=custom" in line
        and "--dbname=local_app" in line
    )
    terminate_index = next(
        index for index, line in enumerate(lines) if "pg_terminate_backend" in line
    )
    assert ps_index < stop_index < safety_index < terminate_index
    assert not any(
        line.startswith(("docker compose up ", "docker compose start ")) and "web" in line
        for line in lines
    )
    assert "Local web service remains stopped" in result.stderr


def test_restore_aborts_before_drop_when_running_web_cannot_be_stopped(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if replacement continued while the normal web service was still writing."""
    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(tmp_path / "backups"),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "WEB_RUNNING": "yes",
            "WEB_STOP_MODE": "fail",
        }
    )

    assert result.returncode != 0
    assert "Could not stop the running local web service" in result.stderr
    commands = _commands(clone_env)
    assert "docker compose stop web" in commands
    assert "pg_terminate_backend" not in commands
    assert "DROP DATABASE" not in commands
    assert "CREATE DATABASE" not in commands


def test_existing_dump_retry_avoids_staging_and_uses_the_guarded_local_restore(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if a retained-dump retry contacted staging or skipped local safety guards."""
    dump_path = _write_existing_staging_dump(tmp_path)
    original_dump = dump_path.read_bytes()
    original_checksum = dump_path.with_suffix(".dump.sha256").read_bytes()
    env = {
        **clone_env,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "CONFIRM_REPLACE_LOCAL_DB": "yes",
        "STAGING_DUMP_FILE": str(dump_path),
    }
    env.pop("STAGING_SSH_TARGET")

    result = _run(env=env)

    assert result.returncode == 0, result.stderr
    assert dump_path.read_bytes() == original_dump
    assert dump_path.with_suffix(".dump.sha256").read_bytes() == original_checksum
    commands = _commands(env)
    assert "ssh " not in commands
    assert f"--volume {dump_path}:/dump:ro" in commands
    assert "docker compose up -d db" in commands
    assert "docker compose exec -T db pg_dump --format=custom" in commands
    assert "docker compose exec -T db pg_restore --exit-on-error" in commands
    assert len(list((tmp_path / "backups").glob("*.local-safety.dump"))) == 1


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("prepare_dump", "extra_env", "expected_message"),
    [
        (
            lambda dump_path: dump_path.with_suffix(".dump.sha256").unlink(),
            {},
            "checksum is missing",
        ),
        (
            lambda dump_path: dump_path.with_suffix(".dump.sha256").write_text(
                "0" * 64 + f"  {dump_path.name}\n", encoding="utf-8"
            ),
            {},
            "checksum validation failed",
        ),
        (_replace_checksum_with_other_file, {}, "checksum validation failed"),
        (_append_another_checksum_entry, {}, "checksum validation failed"),
        (_write_malformed_checksum, {}, "checksum validation failed"),
        (lambda dump_path: None, {"PG_RESTORE_MODE": "fail"}, "Could not validate staging dump"),
    ],
    ids=(
        "missing-checksum",
        "bad-checksum",
        "sidecar-checks-other-file",
        "extra-checksum-entry",
        "malformed-checksum",
        "invalid-archive",
    ),
)
def test_existing_dump_retry_rejects_untrusted_input_before_local_database_mutation(
    clone_env: dict[str, str],
    tmp_path: Path,
    prepare_dump: Callable[[Path], None],
    extra_env: dict[str, str],
    expected_message: str,
) -> None:
    """Would fail if unchecked retained data could reach local replacement."""
    dump_path = _write_existing_staging_dump(tmp_path)
    prepare_dump(dump_path)
    env = {
        **clone_env,
        **extra_env,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "CONFIRM_REPLACE_LOCAL_DB": "yes",
        "STAGING_DUMP_FILE": str(dump_path),
    }
    env.pop("STAGING_SSH_TARGET")

    result = _run(env=env)

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert "ssh " not in _commands(env)
    _assert_local_database_was_not_touched(env)


def test_migration_validation_uses_one_off_read_only_web_containers(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if validation started the normal web entrypoint or ran migrations."""
    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(tmp_path / "backups"),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode == 0, result.stderr
    validation_lines = _web_validation_lines(clone_env)
    assert len(validation_lines) == 3
    assert all("--rm --no-deps --entrypoint python web" in line for line in validation_lines)
    assert any("manage.py shell -c " in line for line in validation_lines)
    assert any("manage.py showmigrations --plan" in line for line in validation_lines)
    assert any("manage.py makemigrations --check --dry-run" in line for line in validation_lines)
    commands = _commands(clone_env)
    assert "manage.py migrate" not in commands
    assert "bootstrap_photographer_group" not in commands
    assert not any(
        line.startswith("docker compose up ") and " web" in line for line in commands.splitlines()
    )


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("database_state", "expected_message"),
    [
        ("no-applied-migrations", "no applied Django migrations"),
        ("migrations-ahead-of-checkout", "absent from this checkout; update the branch"),
        ("invalid-migration-table", "validate django_migrations"),
        ("connectivity-failure", "validate django_migrations"),
        ("pending-migrations", "not a clean migration baseline"),
        ("model-drift", "Migration model validation failed; inspect"),
    ],
)
def test_migration_validation_keeps_restored_artifacts_for_actionable_failures(
    clone_env: dict[str, str],
    tmp_path: Path,
    database_state: str,
    expected_message: str,
) -> None:
    """Would fail if an unsafe migration state were reported as a successful clone."""
    backup_dir = tmp_path / "backups"
    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "DJANGO_DATABASE_STATE": database_state,
        }
    )

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    assert len(_web_validation_lines(clone_env)) >= 1
    assert Path(clone_env["PG_RESTORE_COUNT_FILE"]).read_text(encoding="utf-8") == "1\n"
    assert _commands(clone_env).count('DROP DATABASE IF EXISTS "local_app"') == 1


@pytest.mark.clone_staging_slow
def test_invalid_published_local_safety_dump_aborts_before_database_replacement(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if an unreadable local safety archive allowed terminate or drop to begin."""
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "LOCAL_SAFETY_VALIDATION_MODE": "fail",
        }
    )

    assert result.returncode != 0
    assert "Could not validate local safety dump" in result.stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    commands = _commands(clone_env)
    lines = commands.splitlines()
    safety_dump_index = next(
        index
        for index, line in enumerate(lines)
        if "docker compose exec -T db pg_dump --format=custom" in line
        and "--dbname=local_app" in line
    )
    safety_validation_index = next(
        index
        for index, line in enumerate(lines)
        if "docker run --rm --network none --volume" in line
        and ".local-safety.dump:/dump:ro" in line
        and "pg_restore --list /dump" in line
    )
    assert safety_dump_index < safety_validation_index
    assert "pg_terminate_backend" not in commands
    assert "DROP DATABASE" not in commands
    assert "CREATE DATABASE" not in commands
    assert "pg_restore --exit-on-error" not in commands


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("restore_mode", "expected_message"),
    [
        ("success", "Local database recovery from safety dump succeeded"),
        ("all-fail", "Local database recovery from safety dump failed"),
    ],
    ids=("recovery-succeeds", "recovery-fails"),
)
def test_signal_during_replacement_recovers_from_retained_safety_dump(
    clone_env: dict[str, str], tmp_path: Path, restore_mode: str, expected_message: str
) -> None:
    """Would fail if a destructive-phase signal exited without restoring the old local state."""
    backup_dir = tmp_path / "backups"
    process = subprocess.Popen(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={
            **os.environ,
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "PSQL_PAUSE_AT": "2",
            "LOCAL_RESTORE_MODE": restore_mode,
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pause_path = Path(clone_env["PSQL_PAUSE_FILE"])
    deadline = time.monotonic() + 15
    while not pause_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pause_path.exists()

    process.send_signal(signal.SIGHUP)
    Path(clone_env["PSQL_CONTINUE_FILE"]).touch()
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 129, stdout + stderr
    assert "Signal received during local database replacement" in stderr
    assert expected_message in stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    commands = _commands(clone_env)
    assert commands.count('DROP DATABASE IF EXISTS "local_app"') == 2
    assert commands.count('CREATE DATABASE "local_app" OWNER "local_user"') == 1
    assert commands.count("pg_restore --exit-on-error") == 1


@pytest.mark.clone_staging_slow
def test_signal_during_connection_termination_does_not_replace_local_database(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if a signal before DROP caused recovery to destroy an intact local database."""
    backup_dir = tmp_path / "backups"
    process = subprocess.Popen(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={
            **os.environ,
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "PSQL_PAUSE_AT": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pause_path = Path(clone_env["PSQL_PAUSE_FILE"])
    deadline = time.monotonic() + 15
    while not pause_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pause_path.exists()

    process.send_signal(signal.SIGHUP)
    Path(clone_env["PSQL_CONTINUE_FILE"]).touch()
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 129, stdout + stderr
    assert "recovery from safety dump" not in stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    commands = _commands(clone_env)
    assert commands.count("pg_terminate_backend") == 1
    assert "DROP DATABASE" not in commands
    assert "CREATE DATABASE" not in commands
    assert "pg_restore --exit-on-error" not in commands


@pytest.mark.clone_staging_slow
def test_signal_during_django_validation_retains_the_successfully_restored_database(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if a post-restore validation signal recovered the old local database."""
    backup_dir = tmp_path / "backups"
    process = subprocess.Popen(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={
            **os.environ,
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "DJANGO_PAUSE_MODE": "shell",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pause_path = Path(clone_env["DJANGO_PAUSE_FILE"])
    deadline = time.monotonic() + 15
    while not pause_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pause_path.exists()

    process.send_signal(signal.SIGHUP)
    Path(clone_env["DJANGO_CONTINUE_FILE"]).touch()
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 129, stdout + stderr
    assert "recovery from safety dump" not in stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    commands = _commands(clone_env)
    assert commands.count('DROP DATABASE IF EXISTS "local_app"') == 1
    assert commands.count("pg_restore --exit-on-error") == 1


def test_restore_failure_recovers_local_database_from_retained_safety_dump(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if a staging restore failure leaves local data replaced or exits successfully."""
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "LOCAL_RESTORE_MODE": "staging-fail",
        }
    )

    assert result.returncode != 0
    assert "Staging restore failed" in result.stderr
    assert "Local database recovery from safety dump succeeded" in result.stderr
    assert "Local database replacement completed" not in result.stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    assert Path(clone_env["PG_RESTORE_COUNT_FILE"]).read_text(encoding="utf-8") == "2\n"
    assert _commands(clone_env).count('DROP DATABASE IF EXISTS "local_app"') == 2


@pytest.mark.clone_staging_slow
def test_restore_failure_reports_when_safety_recovery_also_fails(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if a failed recovery were reported as a successful clone or discarded evidence."""
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "LOCAL_RESTORE_MODE": "all-fail",
        }
    )

    assert result.returncode != 0
    assert "Staging restore failed" in result.stderr
    assert "Local database recovery from safety dump failed" in result.stderr
    assert "Local database replacement completed" not in result.stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    assert Path(clone_env["PG_RESTORE_COUNT_FILE"]).read_text(encoding="utf-8") == "2\n"


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("restore_mode", "expected_message"),
    [
        ("success", "Local database recovery from safety dump succeeded"),
        ("all-fail", "Local database recovery from safety dump failed"),
    ],
    ids=("create-failure-recovery-succeeds", "create-failure-recovery-fails"),
)
def test_create_failure_after_drop_attempts_exact_safety_dump_recovery_and_stays_failed(
    clone_env: dict[str, str],
    tmp_path: Path,
    restore_mode: str,
    expected_message: str,
) -> None:
    """Would fail if CREATE failure exited without restoring the retained local safety dump."""
    backup_dir = tmp_path / "backups"
    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "PSQL_FAIL_AT": "3",
            "LOCAL_RESTORE_MODE": restore_mode,
        }
    )

    assert result.returncode != 0
    assert "Local database recreation failed after DROP began" in result.stderr
    assert expected_message in result.stderr
    assert "Local database replacement completed" not in result.stderr
    assert len(list(backup_dir.glob("*.dump"))) == 2
    assert Path(clone_env["PG_RESTORE_COUNT_FILE"]).read_text(encoding="utf-8") == "1\n"
    commands = _commands(clone_env)
    assert commands.count('DROP DATABASE IF EXISTS "local_app"') == 2
    safety_dump = next(backup_dir.glob("*.local-safety.dump"))
    recovery_restore = [
        line for line in commands.splitlines() if "pg_restore --exit-on-error" in line
    ]
    assert len(recovery_restore) == 1
    assert (
        Path(f"{clone_env['PG_RESTORE_INPUT_PREFIX']}.1").read_bytes() == safety_dump.read_bytes()
    )


@pytest.mark.clone_staging_slow
def test_parallel_clone_fails_on_atomic_project_database_lock_before_ssh_or_sql(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if two real script processes could enter one project/database workflow."""
    backup_dir = tmp_path / "backups"
    first_process = subprocess.Popen(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={
            **os.environ,
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "SSH_PAUSE_MODE": "version",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        pause_path = Path(clone_env["SSH_PAUSE_FILE"])
        deadline = time.monotonic() + 15
        while not pause_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pause_path.exists()

        second_log = tmp_path / "second-commands.log"
        second_result = _run(
            env={
                **clone_env,
                "BACKUP_DIR": str(backup_dir),
                "COMMAND_LOG": str(second_log),
                "CONFIRM_REPLACE_LOCAL_DB": "yes",
            }
        )

        assert second_result.returncode != 0
        assert "clone lock" in second_result.stderr
        assert "rmdir" in second_result.stderr
        second_commands = second_log.read_text(encoding="utf-8")
        assert "ssh " not in second_commands
        assert "docker compose exec" not in second_commands
        assert "docker compose run" not in second_commands
        assert "docker compose up" not in second_commands
    finally:
        Path(clone_env["SSH_CONTINUE_FILE"]).touch()
        stdout, stderr = first_process.communicate(timeout=10)
    assert first_process.returncode == 0, stdout + stderr


@pytest.mark.clone_staging_slow
def test_stale_clone_lock_is_not_removed_automatically(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    """Would fail if a process silently deleted a lock that it did not acquire."""
    backup_dir = tmp_path / "backups"
    lock_key = hashlib.sha256(b"clone-guard-test\x00local_app").hexdigest()
    lock_path = backup_dir / ".locks" / f"{lock_key}.lock"
    lock_path.mkdir(parents=True)

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode != 0
    assert str(lock_path) in result.stderr
    assert "rmdir" in result.stderr
    assert lock_path.is_dir()
    commands = _commands(clone_env)
    assert "ssh " not in commands
    assert "docker compose exec" not in commands


def test_restore_postgres_16_integration_uses_only_an_isolated_local_compose_project(
    tmp_path: Path,
) -> None:
    """Would fail if the restored rows, table owner, or ACL came from the old local database."""
    if os.environ.get("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("set RUN_POSTGRES_INTEGRATION=1 to run the isolated PostgreSQL 16 restore test")

    project = f"clone_staging_restore_{os.getpid()}"
    compose_file = tmp_path / "compose.integration.yml"
    compose_file.write_text(
        """services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: local_app
      POSTGRES_USER: local_user
      POSTGRES_PASSWORD: local-password
    tmpfs:
      - /var/lib/postgresql/data
  web:
    image: busybox:1.36
    environment:
      DB_HOST: db
""",
        encoding="utf-8",
    )
    compose_env = {
        **os.environ,
        "COMPOSE_FILE": str(compose_file),
        "COMPOSE_PROJECT_NAME": project,
    }

    def compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["docker", "compose", *arguments],
            cwd=ROOT,
            env=compose_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result

    try:
        compose("up", "-d", "db")
        deadline = time.monotonic() + 30
        while compose(
            "exec",
            "-T",
            "db",
            "pg_isready",
            "--username=local_user",
            "--dbname=postgres",
            check=False,
        ).returncode:
            assert time.monotonic() < deadline, (
                "isolated PostgreSQL 16 container did not become ready"
            )
            time.sleep(0.2)

        source_owner = f"staging_owner_{os.getpid()}"
        compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--username=local_user",
            "--dbname=local_app",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            " ".join(
                [
                    f"CREATE ROLE {source_owner};",
                    "CREATE TABLE clone_restore_markers (marker text primary key);",
                    "INSERT INTO clone_restore_markers VALUES ('staging-marker');",
                    f"ALTER TABLE clone_restore_markers OWNER TO {source_owner};",
                    "GRANT SELECT ON clone_restore_markers TO PUBLIC;",
                ]
            ),
        )
        staging_dump = tmp_path / "staging.dump"
        with staging_dump.open("wb") as dump_file:
            dump_result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "db",
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--username=local_user",
                    "--dbname=local_app",
                ],
                cwd=ROOT,
                env=compose_env,
                stdout=dump_file,
                stderr=subprocess.PIPE,
                check=False,
            )
        assert dump_result.returncode == 0, dump_result.stderr.decode("utf-8")

        compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--username=local_user",
            "--dbname=local_app",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "DROP TABLE clone_restore_markers; "
            "CREATE TABLE clone_restore_markers (marker text primary key); "
            "INSERT INTO clone_restore_markers VALUES ('old-local-marker');",
        )

        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "docker",
            """
printf '%s\n' "$*" >> "$VALIDATION_DOCKER_LOG"
case "$*" in
  *"manage.py shell -c "*) exit 0 ;;
  *"manage.py showmigrations --plan"*) printf '[X] 0001_initial\\n'; exit 0 ;;
  *"manage.py makemigrations --check --dry-run"*) exit 0 ;;
esac
exec "$REAL_DOCKER" "$@"
""",
        )
        _write_executable(
            fake_bin / "ssh",
            """
case "$*" in
  *'postgres --version'*) printf 'postgres (PostgreSQL) 16.6\\n' ;;
  *'printenv POSTGRES_DB'*) printf 'staging_app\\n' ;;
  *'printenv POSTGRES_USER'*) printf 'staging_user\\n' ;;
  *'pg_dump '*) cat "$INTEGRATION_STAGING_DUMP" ;;
esac
""",
        )
        backup_dir = tmp_path / "backups"
        validation_docker_log = tmp_path / "validation-docker.log"
        result = _run(
            env={
                **compose_env,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "STAGING_SSH_TARGET": "local-test@not-staging.invalid",
                "CONFIRM_REPLACE_LOCAL_DB": "yes",
                "BACKUP_DIR": str(backup_dir),
                "INTEGRATION_STAGING_DUMP": str(staging_dump),
                "REAL_DOCKER": str(shutil.which("docker")),
                "VALIDATION_DOCKER_LOG": str(validation_docker_log),
            }
        )

        assert result.returncode == 0, result.stderr
        validation_commands = [
            line
            for line in validation_docker_log.read_text(encoding="utf-8").splitlines()
            if line.startswith("compose run ") and " web manage.py " in line
        ]
        assert len(validation_commands) == 3
        assert all("--rm --no-deps --entrypoint python web" in line for line in validation_commands)
        assert any("manage.py shell -c " in line for line in validation_commands)
        assert any("manage.py showmigrations --plan" in line for line in validation_commands)
        assert any(
            "manage.py makemigrations --check --dry-run" in line for line in validation_commands
        )
        markers = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--tuples-only",
            "--no-align",
            "--username=local_user",
            "--dbname=local_app",
            "-c",
            "SELECT marker FROM clone_restore_markers ORDER BY marker;",
        )
        assert markers.stdout.strip() == "staging-marker"
        table_state = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--tuples-only",
            "--no-align",
            "--username=local_user",
            "--dbname=local_app",
            "-c",
            "SELECT tableowner || '|' || COALESCE(relacl::text, '') "
            "FROM pg_tables JOIN pg_class ON relname = tablename "
            "WHERE schemaname = 'public' AND tablename = 'clone_restore_markers';",
        )
        assert table_state.stdout.strip() == "local_user|"
    finally:
        compose("rm", "-s", "-f", "db", check=False)


def test_restore_real_django_postgres_16_integration_validates_migration_readiness(
    tmp_path: Path,
) -> None:
    """Would fail if the real project image could not validate the restored migrated schema."""
    if os.environ.get("RUN_DJANGO_CLONE_INTEGRATION") != "1":
        pytest.skip(
            "set RUN_DJANGO_CLONE_INTEGRATION=1 to run the real Django/PostgreSQL 16 clone test"
        )

    project = f"clone_django_restore_{os.getpid()}"
    compose_file = tmp_path / "compose.django-integration.yml"
    compose_file.write_text(
        f"""services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: local_app
      POSTGRES_USER: local_user
      POSTGRES_PASSWORD: local-password
    tmpfs:
      - /var/lib/postgresql/data
  web:
    build:
      context: {ROOT}
    environment:
      SECRET_KEY: integration-only-secret
      DEBUG: "False"
      ALLOWED_HOSTS: localhost
      DB_NAME: local_app
      DB_USER: local_user
      DB_PASSWORD: local-password
      DB_HOST: db
      DB_PORT: "5432"
    depends_on:
      - db
""",
        encoding="utf-8",
    )
    compose_env = {
        **os.environ,
        "COMPOSE_FILE": str(compose_file),
        "COMPOSE_PROJECT_NAME": project,
    }

    def compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["docker", "compose", *arguments],
            cwd=ROOT,
            env=compose_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result

    try:
        compose("build", "web")
        compose("up", "-d", "db")
        deadline = time.monotonic() + 30
        while compose(
            "exec",
            "-T",
            "db",
            "pg_isready",
            "--username=local_user",
            "--dbname=postgres",
            check=False,
        ).returncode:
            assert time.monotonic() < deadline, (
                "isolated PostgreSQL 16 container did not become ready"
            )
            time.sleep(0.2)

        setup_migrate = compose(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "web",
            "manage.py",
            "migrate",
            "--noinput",
        )
        assert "Applying" in setup_migrate.stdout
        source_migration_count = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--tuples-only",
            "--no-align",
            "--username=local_user",
            "--dbname=local_app",
            "-c",
            "SELECT count(*) FROM django_migrations;",
        ).stdout.strip()
        assert int(source_migration_count) > 0

        source_owner = f"staging_django_owner_{os.getpid()}"
        compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--username=local_user",
            "--dbname=local_app",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            " ".join(
                [
                    f"CREATE ROLE {source_owner};",
                    "CREATE TABLE clone_restore_markers (marker text primary key);",
                    "INSERT INTO clone_restore_markers VALUES ('staging-marker');",
                    f"ALTER TABLE clone_restore_markers OWNER TO {source_owner};",
                    "GRANT SELECT ON clone_restore_markers TO PUBLIC;",
                ]
            ),
        )
        staging_dump = tmp_path / "django-staging.dump"
        with staging_dump.open("wb") as dump_file:
            dump_result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "db",
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--username=local_user",
                    "--dbname=local_app",
                ],
                cwd=ROOT,
                env=compose_env,
                stdout=dump_file,
                stderr=subprocess.PIPE,
                check=False,
            )
        assert dump_result.returncode == 0, dump_result.stderr.decode("utf-8")

        compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--username=local_user",
            "--dbname=local_app",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "UPDATE clone_restore_markers SET marker = 'old-local-marker';",
        )

        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        clone_docker_log = tmp_path / "clone-docker.log"
        clone_ssh_log = tmp_path / "clone-ssh.log"
        _write_executable(
            fake_bin / "docker",
            """
printf '%s\n' "$*" >> "$CLONE_DOCKER_LOG"
exec "$REAL_DOCKER" "$@"
""",
        )
        _write_executable(
            fake_bin / "ssh",
            """
printf '%s\n' "$*" >> "$CLONE_SSH_LOG"
case "$*" in
  *'postgres --version'*) printf 'postgres (PostgreSQL) 16.6\\n' ;;
  *'printenv POSTGRES_DB'*) printf 'staging_app\\n' ;;
  *'printenv POSTGRES_USER'*) printf 'staging_user\\n' ;;
  *'pg_dump '*) cat "$INTEGRATION_STAGING_DUMP" ;;
esac
""",
        )
        backup_dir = tmp_path / "backups"
        result = _run(
            env={
                **compose_env,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "STAGING_SSH_TARGET": "local-fixture@no-staging-network.invalid",
                "CONFIRM_REPLACE_LOCAL_DB": "yes",
                "BACKUP_DIR": str(backup_dir),
                "INTEGRATION_STAGING_DUMP": str(staging_dump),
                "REAL_DOCKER": str(shutil.which("docker")),
                "CLONE_DOCKER_LOG": str(clone_docker_log),
                "CLONE_SSH_LOG": str(clone_ssh_log),
            }
        )

        assert result.returncode == 0, result.stderr
        clone_docker_commands = clone_docker_log.read_text(encoding="utf-8")
        assert "manage.py migrate" not in clone_docker_commands
        assert "compose up -d web" not in clone_docker_commands
        assert "compose start web" not in clone_docker_commands
        assert (
            clone_docker_commands.count(
                "compose run --rm --no-deps --entrypoint python web manage.py"
            )
            == 3
        )
        assert "manage.py shell -c " in clone_docker_commands
        assert "manage.py showmigrations --plan" in clone_docker_commands
        assert "manage.py makemigrations --check --dry-run" in clone_docker_commands
        ssh_commands = clone_ssh_log.read_text(encoding="utf-8")
        assert ssh_commands.count("local-fixture@no-staging-network.invalid") == 4

        markers = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--tuples-only",
            "--no-align",
            "--username=local_user",
            "--dbname=local_app",
            "-c",
            "SELECT marker FROM clone_restore_markers ORDER BY marker;",
        )
        assert markers.stdout.strip() == "staging-marker"
        restored_migration_count = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--tuples-only",
            "--no-align",
            "--username=local_user",
            "--dbname=local_app",
            "-c",
            "SELECT count(*) FROM django_migrations;",
        )
        assert restored_migration_count.stdout.strip() == source_migration_count
        table_state = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--tuples-only",
            "--no-align",
            "--username=local_user",
            "--dbname=local_app",
            "-c",
            "SELECT tableowner || '|' || COALESCE(relacl::text, '') "
            "FROM pg_tables JOIN pg_class ON relname = tablename "
            "WHERE schemaname = 'public' AND tablename = 'clone_restore_markers';",
        )
        assert table_state.stdout.strip() == "local_user|"

        migration_plan = compose(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "web",
            "manage.py",
            "showmigrations",
            "--plan",
        )
        assert "[X]" in migration_plan.stdout
        assert "[ ]" not in migration_plan.stdout
        compose(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "web",
            "manage.py",
            "makemigrations",
            "--check",
            "--dry-run",
        )
        assert not compose("ps", "--status", "running", "--services", "web").stdout.strip()
    finally:
        compose("rm", "-s", "-f", "web", "db", check=False)


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("extra_env", "expected_message"),
    [
        ({"SSH_DUMP_MODE": "interrupted"}, "Staging dump stream failed"),
        ({"SSH_DUMP_MODE": "empty"}, "Staging dump was empty"),
        (
            {"SSH_DUMP_MODE": "truncated", "PG_RESTORE_MODE": "fail"},
            "Could not validate staging dump",
        ),
        ({"DF_MODE": "insufficient"}, "Not enough free space"),
        ({"REMOTE_POSTGRES_VERSION": "15.9"}, "Staging PostgreSQL must be major 16"),
        ({"LOCAL_POSTGRES_VERSION": "15.9"}, "Local PostgreSQL must be major 16"),
    ],
    ids=("interrupted", "empty", "truncated", "insufficient-space", "remote-major", "local-major"),
)
def test_dump_pre_restore_failure_preserves_the_local_database(
    clone_env: dict[str, str], tmp_path: Path, extra_env: dict[str, str], expected_message: str
) -> None:
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            **extra_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert not _published_backup_artifacts(backup_dir)
    _assert_local_database_was_not_touched(clone_env)


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize("failed_move", (1, 2, 3))
def test_dump_publication_rename_failure_leaves_no_partial_artifacts(
    clone_env: dict[str, str], tmp_path: Path, failed_move: int
) -> None:
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "MV_FAIL_AT": str(failed_move),
        }
    )

    assert result.returncode != 0
    assert "Could not publish staging dump" in result.stderr
    assert not _published_backup_artifacts(backup_dir)
    _assert_local_database_was_not_touched(clone_env)


@pytest.mark.clone_staging_slow
def test_dump_publication_hup_terminates_without_published_artifacts(
    clone_env: dict[str, str], tmp_path: Path
) -> None:
    backup_dir = tmp_path / "backups"
    process = subprocess.Popen(
        ["sh", SCRIPT],
        cwd=ROOT,
        env={
            **os.environ,
            **clone_env,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
            "MV_PAUSE_AT": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pause_path = Path(clone_env["MV_PAUSE_FILE"])
    deadline = time.monotonic() + 15
    while not pause_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pause_path.exists()

    process.send_signal(signal.SIGHUP)
    Path(clone_env["MV_CONTINUE_FILE"]).touch()
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 129, stdout + stderr
    assert not _published_backup_artifacts(backup_dir)
    _assert_local_database_was_not_touched(clone_env)


@pytest.mark.clone_staging_slow
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("REMOTE_DATABASE", "staging\napp"),
        ("REMOTE_DATABASE", "staging\x1bapp"),
        ("REMOTE_USER", "staging\x7fuser"),
    ],
    ids=("database-lf", "database-escape", "user-del"),
)
def test_dump_rejects_remote_coordinate_control_characters_before_dump(
    clone_env: dict[str, str], tmp_path: Path, field: str, value: str
) -> None:
    backup_dir = tmp_path / "backups"

    result = _run(
        env={
            **clone_env,
            field: value,
            "BACKUP_DIR": str(backup_dir),
            "CONFIRM_REPLACE_LOCAL_DB": "yes",
        }
    )

    assert result.returncode != 0
    assert "unsupported control characters" in result.stderr
    assert "pg_dump" not in _commands(clone_env)
    assert not _published_backup_artifacts(backup_dir)
    _assert_local_database_was_not_touched(clone_env)
