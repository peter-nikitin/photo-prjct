# ruff: noqa: E501

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/cutover-compose-identity.sh"
SOURCE_VOLUMES = "\n".join(
    (
        "photo-prjct-staging_pgdata",
        "photo-prjct-staging_letsencrypt",
        "photo-prjct-staging_certbot-webroot",
    )
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _cutover_env(tmp_path: Path, *, state: str = "ready") -> dict[str, str]:
    deploy_root = tmp_path / "deployment"
    fake_bin = tmp_path / "bin"
    deploy_root.mkdir()
    fake_bin.mkdir()
    worker_replicas = "2" if state == "two-worker-rollback" else "1"
    (deploy_root / ".env").write_text(
        f"APP_IMAGE=old-image\nWORKER_IMAGE=old-worker-image\nPHOTO_PROCESSING_ENABLED=True\nPHOTO_WORKER_REPLICAS={worker_replicas}\n",
        encoding="utf-8",
    )
    for compose_file in ("docker-compose.deployment.yml", "docker-compose.https.yml"):
        (deploy_root / compose_file).write_text("services: {}\n", encoding="utf-8")
    (deploy_root / "deploy").mkdir()
    _write_executable(
        deploy_root / "deploy" / "apply-deployment.sh",
        """
        [ "$COMPOSE_PROJECT_NAME" = photo-prjct ]
        [ "$SECRET_KEY" = projected-secret ]
        [ "$DB_PASSWORD" = projected-password ]
        [ "$LETSENCRYPT_EMAIL" = ops@example.test ]
        printf 'apply:%s\\n' "$COMPOSE_PROJECT_NAME" >> "$COMMAND_LOG"
        [ "$CUTOVER_STATE" != generic-deploy-failure ] && [ "$CUTOVER_STATE" != two-worker-rollback ] && [ "$CUTOVER_STATE" != rollback-failure ] && [ "$CUTOVER_STATE" != candidate-rollback ]
        """,
    )
    _write_executable(
        fake_bin / "docker",
        """
        printf '%s\\n' "$*" >> "$COMMAND_LOG"
        case " $* " in
          *" volume ls "*)
            case " $* " in
              *" --filter "*)
                [ "$CUTOVER_STATE" != unlabeled-source ] || {
                  printf '%s\\n' photo-prjct-staging_pgdata photo-prjct-staging_certbot-webroot
                  exit 0
                }
                ;;
            esac
            printf '%s\\n' "$SOURCE_VOLUMES"
            [ "$CUTOVER_STATE" != destination-exists ] || printf '%s\\n' photo-prjct_pgdata
            ;;
          *" volume inspect "*"photo-prjct-staging"*)
            ;;
          *" volume inspect "*"photo-prjct_"*)
            [ "$CUTOVER_STATE" = destination-unlabeled ]
            ;;
          *" ps "*"photo-prjct-staging"*)
            [ "$CUTOVER_STATE" != writers-running ] || printf 'photo-prjct-staging-web-1\\n'
            ;;
          *" ps -a "*)
            case " $* " in
              *" --filter "*) ;;
              *)
                [ "$CUTOVER_STATE" != source-namespace ] || printf '%s\\n' photo-prjct-staging-db-1 photo-prjct-staging-nginx-1
                [ "$CUTOVER_STATE" != destination-container ] || printf 'photo-prjct-web\\n'
                ;;
            esac
            ;;
          *" ps "*)
            [ "$CUTOVER_STATE" != writers-running ] || printf 'photo-prjct-staging-web-1\\n'
            ;;
          *" network ls "*)
            case " $* " in
              *" --filter "*) ;;
              *)
                [ "$CUTOVER_STATE" != source-namespace ] || printf 'photo-prjct-staging_default\\n'
                [ "$CUTOVER_STATE" != destination-network ] || printf 'photo-prjct_default\\n'
                ;;
            esac
            ;;
          *" run "*"tar -czf /backup/certificates.tar.gz"*)
            for argument in "$@"; do
              case "$argument" in
                *:/backup) backup_directory=${argument%:/backup} ;;
              esac
            done
            if [ "$CUTOVER_STATE" = certbot-symlink ] || [ "$CUTOVER_STATE" = certbot-broken-link ]; then
              certificate_root=$(mktemp -d)
              mkdir -p "$certificate_root/letsencrypt/archive/photo-prjct" "$certificate_root/letsencrypt/live/photo-prjct" "$certificate_root/certbot-webroot"
              printf certificate > "$certificate_root/letsencrypt/archive/photo-prjct/fullchain1.pem"
              printf key > "$certificate_root/letsencrypt/archive/photo-prjct/privkey1.pem"
              ln -s ../../archive/photo-prjct/fullchain1.pem "$certificate_root/letsencrypt/live/photo-prjct/fullchain.pem"
              if [ "$CUTOVER_STATE" = certbot-broken-link ]; then
                ln -s ../../archive/photo-prjct/missing.pem "$certificate_root/letsencrypt/live/photo-prjct/privkey.pem"
              else
                ln -s ../../archive/photo-prjct/privkey1.pem "$certificate_root/letsencrypt/live/photo-prjct/privkey.pem"
              fi
              tar -czf "$backup_directory/certificates.tar.gz" -C "$certificate_root" letsencrypt certbot-webroot
              rm -rf "$certificate_root"
            else
              printf 'fake-certificate-backup\\n' > "$backup_directory/certificates.tar.gz"
            fi
            ;;
          *" compose "*" printenv POSTGRES_USER "*) printf 'photo\\n' ;;
          *" compose "*" printenv POSTGRES_DB "*) printf 'photo\\n' ;;
          *" compose "*" pg_dump "*)
            case " $* " in *" --username photo --dbname photo "*) printf 'fake-postgresql-dump\\n' ;; *) exit 71 ;; esac
            ;;
          *" compose "*" pg_restore "*)
            if [ "$CUTOVER_STATE" = signal-term ]; then
              printf ready > "$SIGNAL_READY"
              while :; do sleep 1; done
            fi
            [ "$CUTOVER_STATE" != restore-failure ] || exit 72
            case " $* " in *" --username photo --dbname photo "*) ;; *) exit 73 ;; esac
            ;;
          *" compose "*" psql "*)
            case " $* " in *" --username photo --dbname photo "*) ;; *) exit 74 ;; esac
            case " $* " in
              *"information_schema.tables"*) printf 'alpha\\nbeta\\n' ;;
              *"alpha"*)
                case " $* " in *"--project-name photo-prjct "*) [ "$CUTOVER_STATE" != row-count-failure ] && printf '2\\n' || printf '3\\n' ;; *) printf '2\\n' ;; esac
                ;;
              *"beta"*) printf '4\\n' ;;
            esac
            ;;
          *" compose "*" migrate --check "*) [ "$CUTOVER_STATE" != migration-failure ] ;;
          *" run "*"tar -tzf /backup/certificates.tar.gz"*)
            [ "$CUTOVER_STATE" != unrelated-certificate-archive ] || exit 75
            if [ "$CUTOVER_STATE" = certbot-symlink ] || [ "$CUTOVER_STATE" = certbot-broken-link ]; then
              for argument in "$@"; do case "$argument" in *:/backup:ro) backup_directory=${argument%:/backup:ro} ;; esac; done
              tar -tzf "$backup_directory/certificates.tar.gz"
            else
              printf '%s\\n' letsencrypt/live/photo-prjct/fullchain.pem letsencrypt/live/photo-prjct/privkey.pem
            fi
            ;;
          *" run "*"mkdir /verify && tar -xzf /backup/certificates.tar.gz"*)
            if [ "$CUTOVER_STATE" = certbot-symlink ] || [ "$CUTOVER_STATE" = certbot-broken-link ]; then
              for argument in "$@"; do case "$argument" in *:/backup:ro) backup_directory=${argument%:/backup:ro} ;; esac; done
              certificate_root=$(mktemp -d)
              tar -xzf "$backup_directory/certificates.tar.gz" -C "$certificate_root"
              test -r "$certificate_root/letsencrypt/live/photo-prjct/fullchain.pem" && test -s "$certificate_root/letsencrypt/live/photo-prjct/fullchain.pem" && test -r "$certificate_root/letsencrypt/live/photo-prjct/privkey.pem" && test -s "$certificate_root/letsencrypt/live/photo-prjct/privkey.pem"
              certificate_status=$?
              rm -rf "$certificate_root"
              exit "$certificate_status"
            fi
            ;;
          *" run "*"tar -xOzf /backup/certificates.tar.gz"*) [ "$CUTOVER_STATE" != unrelated-certificate-archive ] ;;
          *" run "*"test -s /etc/letsencrypt/live/photo-prjct/fullchain.pem"*) [ "$CUTOVER_STATE" != restored-certificate-missing ] ;;
          *" compose "*"--project-name photo-prjct-staging "*" up -d "*)
            [ "$CUTOVER_STATE" != rollback-failure ] || exit 76
            if [ "$CUTOVER_STATE" = candidate-rollback ]; then
              printf 'source-recovery app=%s worker=%s processing=%s\\n' "${APP_IMAGE-unset}" "${WORKER_IMAGE-unset}" "${PHOTO_PROCESSING_ENABLED-unset}" >> "$COMMAND_LOG"
              if [ -n "${APP_IMAGE+x}" ] || [ -n "${WORKER_IMAGE+x}" ] || [ -n "${PHOTO_PROCESSING_ENABLED+x}" ]; then
                exit 77
              fi
              [ "$(sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env")" = old-image ] || exit 78
              [ "$(sed -n 's/^WORKER_IMAGE=//p' "$DEPLOY_ROOT/.env")" = old-worker-image ] || exit 79
              exit 0
            fi
            ;;
        esac
        """,
    )
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (tmp_path / "commands.log").touch()
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOY_ROOT": str(deploy_root),
        "DB_NAME": "photo",
        "DB_USER": "photo",
        "SECRET_KEY": "projected-secret",
        "DB_PASSWORD": "projected-password",
        "LETSENCRYPT_EMAIL": "ops@example.test",
        "COMMAND_LOG": str(tmp_path / "commands.log"),
        "SOURCE_VOLUMES": SOURCE_VOLUMES,
        "CUTOVER_STATE": state,
        "BACKUP_DIR": str(backup_dir),
        "SIGNAL_READY": str(tmp_path / "signal-ready"),
    }


def _run(*arguments: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", SCRIPT, *arguments],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def test_dry_run_accepts_only_the_exact_empty_cutover_state(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path)

    result = _run("--dry-run", "--backup-dir", env["BACKUP_DIR"], env=env)

    assert result.returncode == 0, result.stderr
    assert "DRY RUN: source volumes are preserved" in result.stdout
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "volume create" not in commands
    assert " pg_dump " not in commands


def test_dry_run_accepts_the_real_unlabeled_letsencrypt_source_volume(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path, state="unlabeled-source")

    result = _run("--dry-run", "--backup-dir", env["BACKUP_DIR"], env=env)

    assert result.returncode == 0, result.stderr


def test_dry_run_does_not_confuse_the_old_project_namespace_with_the_destination(
    tmp_path: Path,
) -> None:
    env = _cutover_env(tmp_path, state="source-namespace")

    result = _run("--dry-run", "--backup-dir", env["BACKUP_DIR"], env=env)

    assert result.returncode == 0, result.stderr


def test_confirmed_cutover_backups_before_creating_canonical_volumes_and_uses_generic_deploy(
    tmp_path: Path,
) -> None:
    env = _cutover_env(tmp_path)

    result = _run(
        "--confirm-canonical-compose-identity-cutover",
        "--backup-dir",
        env["BACKUP_DIR"],
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (Path(env["BACKUP_DIR"]) / "postgresql.dump").read_text(encoding="utf-8") == (
        "fake-postgresql-dump\n"
    )
    assert (Path(env["BACKUP_DIR"]) / "certificates.tar.gz").is_file()
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert commands.index("pg_dump") < commands.index("volume create")
    assert "volume inspect photo-prjct-staging_pgdata" in commands
    assert "photo-prjct_pgdata" in commands
    assert "migrate --check" in commands
    assert "certificates" in commands
    assert "apply:photo-prjct" in commands
    assert "--username photo --dbname photo" in commands
    assert "stop nginx certbot" in commands
    assert "volume rm" not in commands
    assert "volume create photo-prjct-staging" not in commands


def test_confirmed_cutover_accepts_a_regular_readable_non_executable_generic_entrypoint(
    tmp_path: Path,
) -> None:
    env = _cutover_env(tmp_path)
    entrypoint = Path(env["DEPLOY_ROOT"]) / "deploy" / "apply-deployment.sh"
    entrypoint.chmod(0o644)

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode == 0, result.stderr
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "apply:photo-prjct" in commands


@pytest.mark.parametrize("entrypoint_kind", ["missing", "directory"])
def test_cutover_rejects_a_missing_or_nonregular_generic_entrypoint(
    tmp_path: Path, entrypoint_kind: str
) -> None:
    env = _cutover_env(tmp_path)
    entrypoint = Path(env["DEPLOY_ROOT"]) / "deploy" / "apply-deployment.sh"
    if entrypoint_kind == "missing":
        entrypoint.unlink()
    else:
        entrypoint.unlink()
        entrypoint.mkdir()

    result = _run("--dry-run", "--backup-dir", env["BACKUP_DIR"], env=env)

    assert result.returncode != 0
    assert "generic deployment entrypoint is required" in result.stderr
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "volume create" not in commands


@pytest.mark.parametrize(
    ("state", "arguments", "message"),
    [
        ("destination-exists", ("--dry-run",), "canonical destination volumes already exist"),
        ("destination-unlabeled", ("--dry-run",), "canonical destination volume already exists"),
        ("destination-container", ("--dry-run",), "canonical destination containers already exist"),
        ("destination-network", ("--dry-run",), "canonical destination networks already exist"),
        ("writers-running", ("--dry-run",), "web or worker writers are still running"),
        ("ready", ("--source-project", "anything"), "Usage:"),
        ("ready", ("--dry-run", "--backup-dir", "relative-backups"), "absolute path"),
    ],
)
def test_cutover_fails_closed_before_any_volume_mutation(
    tmp_path: Path, state: str, arguments: tuple[str, ...], message: str
) -> None:
    env = _cutover_env(tmp_path, state=state)
    command = list(arguments)
    if "--backup-dir" not in command:
        command.extend(("--backup-dir", env["BACKUP_DIR"]))

    result = _run(*command, env=env)

    assert result.returncode != 0
    assert message in result.stderr
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "volume create" not in commands
    assert "volume rm" not in commands


def test_cutover_rejects_an_unexpected_source_volume(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path)
    env["SOURCE_VOLUMES"] = f"{SOURCE_VOLUMES}\nphoto-prjct-staging_unknown"

    result = _run("--dry-run", "--backup-dir", env["BACKUP_DIR"], env=env)

    assert result.returncode != 0
    assert "unexpected source volumes" in result.stderr


@pytest.mark.parametrize(
    "state",
    ["restore-failure", "row-count-failure", "migration-failure", "generic-deploy-failure"],
)
def test_mutation_failure_stops_canonical_and_restarts_the_exact_source_project(
    tmp_path: Path, state: str
) -> None:
    env = _cutover_env(tmp_path, state=state)

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode != 0
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "--project-name photo-prjct " in commands
    assert " down --remove-orphans" in commands
    assert "--project-name photo-prjct-staging " in commands
    assert "--profile worker up -d --scale worker=1 --remove-orphans" in commands
    assert "volume rm" not in commands


def test_rollback_restores_the_original_two_worker_source_profile(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path, state="two-worker-rollback")

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode != 0
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "--profile worker up -d --scale worker=2 --remove-orphans" in commands


@pytest.mark.parametrize("signal_number", [signal.SIGINT, signal.SIGTERM])
def test_signal_after_mutation_attempts_recovery_and_fails(
    tmp_path: Path, signal_number: int
) -> None:
    env = _cutover_env(tmp_path, state="signal-term")
    process = subprocess.Popen(
        [
            "sh",
            SCRIPT,
            "--confirm-canonical-compose-identity-cutover",
            "--backup-dir",
            env["BACKUP_DIR"],
        ],
        env={**os.environ, **env},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not Path(env["SIGNAL_READY"]).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert Path(env["SIGNAL_READY"]).exists()
    os.killpg(process.pid, signal_number)
    process.communicate(timeout=5)

    assert process.returncode != 0
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert " down --remove-orphans" in commands
    assert "--project-name photo-prjct-staging " in commands


def test_rollback_failure_is_reported(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path, state="rollback-failure")

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode != 0
    assert "recovery failed" in result.stderr


def test_rollback_clears_candidate_compose_interpolation_before_restarting_source(
    tmp_path: Path,
) -> None:
    env = _cutover_env(tmp_path, state="candidate-rollback")
    env.update(
        APP_IMAGE="candidate-image",
        WORKER_IMAGE="candidate-worker-image",
        PHOTO_PROCESSING_ENABLED="False",
    )

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode != 0
    commands = Path(env["COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "recovery failed" not in result.stderr, commands
    assert "source-recovery app=unset worker=unset processing=unset" in commands


@pytest.mark.parametrize("state", ["unrelated-certificate-archive", "restored-certificate-missing"])
def test_cutover_rejects_a_backup_or_restored_volume_without_the_required_certificate(
    tmp_path: Path, state: str
) -> None:
    env = _cutover_env(tmp_path, state=state)

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode != 0
    assert "certificate" in result.stderr


def test_cutover_accepts_a_real_certbot_live_symlink_archive(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path, state="certbot-symlink")

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode == 0, result.stderr


def test_cutover_rejects_a_certbot_archive_with_a_broken_live_symlink(tmp_path: Path) -> None:
    env = _cutover_env(tmp_path, state="certbot-broken-link")

    result = _run(
        "--confirm-canonical-compose-identity-cutover", "--backup-dir", env["BACKUP_DIR"], env=env
    )

    assert result.returncode != 0
    assert "certificate backup" in result.stderr
