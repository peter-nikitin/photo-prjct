import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PREVIOUS_ENV = (
    b"APP_IMAGE=old-image\n"
    b"SECRET_KEY=old-secret\n"
    b"DEBUG=True\n"
    b"ALLOWED_HOSTS=old.example\n"
    b"DB_NAME=old-app\n"
    b"DB_USER=old-user\n"
    b"DB_PASSWORD=old-password\n"
    b"DB_HOST=old-db\n"
    b"DB_PORT=6543\n"
    b"PUBLIC_DOMAIN=old.example\n"
    b"PHOTO_UPLOAD_ENABLED=True\n"
    b"PRIVATE_MEDIA_S3_BUCKET=old-private-bucket\n"
    b"KEEP_EXACTLY=old-only-setting\n"
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _write_python_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _run(script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", ROOT / script],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _certificate_env(
    tmp_path: Path,
    fake_bin: Path,
    *,
    complete: bool,
    alias: str = "www.findme-photo.ru",
) -> dict[str, str]:
    _write_executable(
        fake_bin / "docker",
        """
printf '%s\n' "$*" >> "$COMMAND_LOG"
case " $* " in
  *" --entrypoint sh "*) [ "$CERT_COMPLETE" = yes ] ;;
esac
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(tmp_path / "docker.log"),
        "CERT_COMPLETE": "yes" if complete else "no",
        "COMPOSE_PROJECT_NAME": "photo-test",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": alias,
        "LETSENCRYPT_EMAIL": "ops@example.com",
    }


def test_existing_certificate_skips_issuance(tmp_path: Path, fake_bin: Path) -> None:
    result = _run(
        "deploy/certbot/reconcile-certificate.sh",
        env=_certificate_env(tmp_path, fake_bin, complete=True),
    )

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "--entrypoint sh" in commands
    assert " certonly " not in f" {commands} "


@pytest.mark.parametrize(
    ("alias", "expected_domains"),
    [("www.findme-photo.ru", 2), ("", 1)],
)
def test_missing_certificate_is_issued_once_for_configured_hosts(
    tmp_path: Path, fake_bin: Path, alias: str, expected_domains: int
) -> None:
    result = _run(
        "deploy/certbot/reconcile-certificate.sh",
        env=_certificate_env(tmp_path, fake_bin, complete=False, alias=alias),
    )

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "docker.log").read_text(encoding="utf-8").splitlines()
    issuance = [command for command in commands if " certonly " in f" {command} "]
    assert len(issuance) == 1
    command = issuance[0]
    assert "--network host" in command
    assert "certbot/certbot:v2.11.0 certonly --standalone" in command
    assert "--non-interactive --agree-tos --email ops@example.com" in command
    assert "--cert-name photo-prjct" in command
    assert command.count(" -d ") == expected_domains
    assert "-d findme-photo.ru" in command
    assert ("-d www.findme-photo.ru" in command) is bool(alias)
    assert "--force-renewal" not in command


def _public_env(
    tmp_path: Path,
    fake_bin: Path,
    *,
    alias: str = "",
    canonical_code: str = "308",
    canonical_location: str = "https://findme-photo.ru/__edge_verify__?source=deploy",
    health_code: str = "200",
) -> dict[str, str]:
    _write_executable(
        fake_bin / "curl",
        """
printf '%s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *"https://findme-photo.ru/health/"*) printf '%s\n' "$HEALTH_CODE" ;;
  *"http://findme-photo.ru/"*) printf '%s\n%s\n' "$CANONICAL_CODE" "$CANONICAL_LOCATION" ;;
  *"http://www.findme-photo.ru/"*)
    printf '308\nhttps://findme-photo.ru/__edge_verify__?source=deploy\n'
    ;;
  *"https://www.findme-photo.ru/"*)
    printf '308\nhttps://findme-photo.ru/__edge_verify__?source=deploy\n'
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "sleep",
        """
printf 'sleep %s\n' "$*" >> "$COMMAND_LOG"
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(tmp_path / "curl.log"),
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": alias,
        "CANONICAL_CODE": canonical_code,
        "CANONICAL_LOCATION": canonical_location,
        "HEALTH_CODE": health_code,
    }


def test_public_smoke_checks_canonical_edge_and_optional_alias(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run("deploy/verify-public-edge.sh", env=_public_env(tmp_path, fake_bin))

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert "http://findme-photo.ru/__edge_verify__?source=deploy" in commands
    assert "https://findme-photo.ru/health/" in commands
    assert "www.findme-photo.ru" not in commands
    assert "dns.google" not in commands

    alias_result = _run(
        "deploy/verify-public-edge.sh",
        env=_public_env(tmp_path, fake_bin, alias="www.findme-photo.ru"),
    )
    assert alias_result.returncode == 0, alias_result.stderr
    commands = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert "http://www.findme-photo.ru/__edge_verify__?source=deploy" in commands
    assert "https://www.findme-photo.ru/__edge_verify__?source=deploy" in commands


@pytest.mark.parametrize(
    ("overrides", "message", "expected_sleeps"),
    [
        ({"canonical_location": "https://findme-photo.ru/wrong"}, "Location", 0),
        ({"health_code": "503"}, "HTTPS health", 12),
    ],
)
def test_public_smoke_rejects_wrong_redirect_or_unhealthy_https(
    tmp_path: Path,
    fake_bin: Path,
    overrides: dict[str, str],
    message: str,
    expected_sleeps: int,
) -> None:
    result = _run(
        "deploy/verify-public-edge.sh",
        env=_public_env(tmp_path, fake_bin, **overrides),
    )

    assert result.returncode != 0
    assert message in result.stderr
    commands = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert commands.count("sleep 5") == expected_sleeps


def _apply_env(
    tmp_path: Path,
    fake_bin: Path,
    *,
    scenario: str,
    target: str = "production",
) -> dict[str, str]:
    (tmp_path / ".env").write_bytes(PREVIOUS_ENV)
    (tmp_path / ".env").chmod(0o640)
    (tmp_path / "previous-env.expected").write_bytes(PREVIOUS_ENV)
    (tmp_path / "deployed-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / "deployment-target").write_text("old-target\n", encoding="utf-8")
    (tmp_path / "compose-project-name").write_text("old-project\n", encoding="utf-8")
    for name in ("docker-compose.prod.yml", "docker-compose.https.yml"):
        (tmp_path / name).write_text("services: {}\n", encoding="utf-8")
    cert_dir = tmp_path / "deploy" / "certbot"
    cert_dir.mkdir(parents=True)
    _write_executable(
        cert_dir / "reconcile-certificate.sh",
        """
printf 'reconcile-certificate\n' >> "$COMMAND_LOG"
[ "$APPLY_SCENARIO" != certificate-failure ]
""",
    )
    deploy_dir = tmp_path / "deploy"
    shutil.copy2(
        ROOT / "deploy/install-upload-cleanup-cron.sh",
        deploy_dir / "install-upload-cleanup-cron.sh",
    )
    _write_executable(
        deploy_dir / "verify-selfie-observability.sh",
        """
printf 'verify-selfie-observability\n' >> "$COMMAND_LOG"
[ "$APPLY_SCENARIO" != observability-verification-failure ]
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """
printf 'sudo %s\n' "$*" >> "$COMMAND_LOG"
[ "${1-}" = -n ] && shift
if [ "${1-}" = /usr/local/sbin/findme-selfie-observability ]; then
  action="${2-}"
  printf 'observability-%s\n' "$action" >> "$COMMAND_LOG"
  [ "$APPLY_SCENARIO:$action" != sudo-preflight-failure:install ] || exit 1
  if [ "$APPLY_SCENARIO:$action" = observability-install-signal:install ]; then
    kill -TERM "$PPID"
    exit 143
  fi
  [ "${VERIFY_SCENARIO:-}:$action" != unreadable-probe:verify-probe ] || exit 1
  [ "$APPLY_SCENARIO:$action" != observability-commit-failure:commit ] || exit 1
  exit 0
fi
if [ "${1-}" = env ] && [ "${2-}" = -i ]; then
  shift 2
  case "${1-}" in PATH=*) shift ;; esac
  exec env "$@"
fi
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "cmp",
        """
case "$*" in
  *"/usr/local/sbin/findme-selfie-observability"*)
    [ "$APPLY_SCENARIO" != stale-observability-helper ]
    ;;
  *"/usr/local/lib/findme-selfie-observability-package/"*)
    [ "$APPLY_SCENARIO" != stale-observability-package ]
    ;;
  *) exec /usr/bin/cmp "$@" ;;
esac
""",
    )
    _write_executable(
        deploy_dir / "verify-public-edge.sh",
        """
if [ "$APPLY_SCENARIO" = fresh-first-deployment ] || \
   [ "$APPLY_SCENARIO" = fresh-first-health-failure ] || \
   [ "$APPLY_SCENARIO" = fresh-first-marker-failure ]; then
  [ ! -e "$DEPLOY_ROOT/deployed-image" ]
else
  [ "$(cat "$DEPLOY_ROOT/deployed-image")" = old-image ]
fi
[ "$APPLY_SCENARIO" != public-failure ]
printf 'verify-public-edge\n' >> "$COMMAND_LOG"
""",
    )
    gallery_preflight_harness = fake_bin / "gallery-preflight-harness"
    _write_python_executable(
        gallery_preflight_harness,
        r"""
        import os
        import sys
        import types


        command_log = os.environ["COMMAND_LOG"]
        scenario = os.environ["APPLY_SCENARIO"]


        def record(message):
            with open(command_log, "a", encoding="utf-8") as log:
                log.write(f"{message}\n")


        class Event:
            class PublicationStatus:
                PUBLISHED = "published"

            class AccessType:
                FREE = "free"


        class QuerySet:
            def __init__(self):
                self.selects_original_key = False

            def order_by(self, field):
                if field != "id":
                    raise RuntimeError("candidate query must use stable id ordering")
                record("preflight-order-by id")
                return self

            def values_list(self, field, *, flat):
                if field != "original_key" or not flat:
                    raise RuntimeError("candidate query must select the private object key")
                self.selects_original_key = True
                return self

            def first(self):
                record("preflight-first")
                if scenario in {
                    "private-media-success",
                    "private-media-failure",
                    "private-media-config-failure",
                }:
                    if self.selects_original_key:
                        return "originals/eligible-photo"
                    return types.SimpleNamespace(original_key="originals/eligible-photo")
                return None


        class Manager:
            def filter(self, **filters):
                if scenario == "private-media-db-failure":
                    raise ConnectionError("database unavailable detail must stay hidden")
                expected = {
                    "event__publication_status": Event.PublicationStatus.PUBLISHED,
                    "event__access_type": Event.AccessType.FREE,
                    "src": "",
                    "original_key__isnull": False,
                }
                if filters != expected:
                    raise RuntimeError("candidate query changed its eligibility boundary")
                record("preflight-filter eligible-private-photo")
                return QuerySet()


        class Photo:
            objects = Manager()


        class Body:
            def read(self, amount):
                record(f"preflight-read {amount}")
                return b"x"

            def close(self):
                record("preflight-close")


        class PrivateUploadStorage:
            def __init__(self):
                record("preflight-storage-init")
                if scenario == "private-media-config-failure":
                    raise RuntimeError("private config detail must stay hidden")

            def open_final(self, *, key):
                record(f"preflight-open {key}")
                if scenario == "private-media-failure":
                    raise PermissionError("private failure detail must stay hidden")
                return types.SimpleNamespace(body=Body())


        ingestion = types.ModuleType("ingestion")
        ingestion.__path__ = []
        storage = types.ModuleType("ingestion.storage")
        storage.PrivateUploadStorage = PrivateUploadStorage
        picflow = types.ModuleType("picflow")
        picflow.__path__ = []
        models = types.ModuleType("picflow.models")
        models.Event = Event
        models.Photo = Photo
        sys.modules.update(
            {
                "ingestion": ingestion,
                "ingestion.storage": storage,
                "picflow": picflow,
                "picflow.models": models,
            }
        )

        exec(compile(os.environ["GALLERY_PREFLIGHT"], "<gallery-media-preflight>", "exec"))
        """,
    )
    _write_executable(
        fake_bin / "docker",
        """
if [ "${1-}" = volume ] && [ "${2-}" = inspect ]; then
  volume_name="${3-}"
  [ "$volume_name" = "${COMPOSE_PROJECT_NAME}_pgdata" ]
  printf 'volume-inspect %s\n' "$volume_name" >> "$COMMAND_LOG"
  if [ "$APPLY_SCENARIO" = volume-inspection-error ]; then
    printf 'docker socket failure raw detail must stay hidden\n' >&2
    exit 1
  fi
  if [ -f "$DEPLOY_ROOT/.docker-volume-$volume_name" ]; then
    exit 0
  fi
  printf 'Error response from daemon: get %s: no such volume\n' "$volume_name" >&2
  exit 1
fi
compose_env_file=""
previous_argument=""
for argument do
  if [ "$previous_argument" = --env-file ]; then
    compose_env_file="$argument"
  fi
  previous_argument="$argument"
done
validate_candidate_env() {
  candidate_access_key="$(sed -n 's/^PRIVATE_MEDIA_S3_ACCESS_KEY_ID=//p' "$compose_env_file")"
  candidate_secret_key="$(sed -n 's/^PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=//p' "$compose_env_file")"
  [ "$compose_env_file" != "$DEPLOY_ROOT/.env" ]
  [ "$APP_ENV_FILE" = "$compose_env_file" ]
  [ "$(sed -n 's/^APP_IMAGE=//p' "$compose_env_file")" = new-image ]
  [ "$(sed -n 's/^SECRET_KEY=//p' "$compose_env_file")" = "$EXPECTED_REQUESTED_SECRET" ]
  [ "$(sed -n 's/^PRIVATE_MEDIA_S3_BUCKET=//p' "$compose_env_file")" = "$PRIVATE_MEDIA_S3_BUCKET" ]
  [ "$candidate_access_key" = "$PRIVATE_MEDIA_S3_ACCESS_KEY_ID" ]
  [ "$candidate_secret_key" = "$PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY" ]
  case "${EXPECT_CANONICAL_ENV:-present}:$APPLY_SCENARIO" in
    absent:*|present:fresh-first-deployment|present:fresh-first-health-failure|present:fresh-first-marker-failure)
      [ ! -e "$DEPLOY_ROOT/.env" ]
      ;;
    *)
      cmp "$DEPLOY_ROOT/.env" "$PREVIOUS_ENV_EXPECTED"
      ;;
  esac
  printf 'candidate-requested-env-with-canonical-untouched\n' >> "$COMMAND_LOG"
}
validate_migration_preflight_env() {
  validate_candidate_env
  case "$(ls -ld "$compose_env_file")" in
    -rw-------*) ;;
    *) exit 1 ;;
  esac
  printf 'candidate-migration-env-mode-0600\n' >> "$COMMAND_LOG"
}
case " $* " in
  *" run --rm --no-deps -T --entrypoint python web manage.py verify_migration_history "*)
    validate_migration_preflight_env
    case "$APPLY_SCENARIO" in
      fresh-first-deployment)
        printf 'unexpected-fresh-migration-history\n' >> "$COMMAND_LOG"
        exit 99
        ;;
      migration-history-missing)
        [ "$CANDIDATE_MIGRATION_LEDGER" = selfie_search.0003_optional_feedback_contact ]
        [ "$CANDIDATE_MIGRATION_GRAPH" = picflow.0001_initial ]
        printf 'candidate-migration-history applied=%s candidate=%s\n' \
          "$CANDIDATE_MIGRATION_LEDGER" "$CANDIDATE_MIGRATION_GRAPH" >> "$COMMAND_LOG"
        exit 1
        ;;
      migration-history-database-unavailable)
        printf 'candidate-migration-history database-unavailable\n' >> "$COMMAND_LOG"
        exit 1
        ;;
      *)
        printf 'candidate-migration-history\n' >> "$COMMAND_LOG"
        ;;
    esac
    ;;
  *" run --rm --no-deps -T --entrypoint python web manage.py showmigrations --plan "*)
    validate_candidate_env
    printf 'candidate-migration-plan\n' >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != migration-plan-failure ]
    ;;
  *" run --rm --no-deps -T --entrypoint python web manage.py shell"*)
    validate_candidate_env
    for gallery_preflight do :; done
    {
      printf 'APP_IMAGE=%s docker' "${APP_IMAGE-unset}"
      argument_number=1
      for argument do
        if [ "$argument_number" -eq "$#" ]; then
          printf ' <gallery_media_preflight>'
        else
          printf ' %s' "$argument"
        fi
        argument_number=$((argument_number + 1))
      done
      printf '\n'
    } >> "$COMMAND_LOG"
    case " $* " in
      *" manage.py shell --no-imports -c "*) : ;;
      *) printf '8 objects imported automatically (use -v 2 for details).\n' ;;
    esac
    GALLERY_PREFLIGHT="$gallery_preflight" "$GALLERY_PREFLIGHT_HARNESS"
    exit $?
    ;;
esac
case " $* " in
  *" compose "*" pull web"*)
    validate_candidate_env
    ;;
  *" compose "*" stop nginx"*)
    [ "$compose_env_file" = "$DEPLOY_ROOT/.env" ]
    [ "$APP_ENV_FILE" = "$DEPLOY_ROOT/.env" ]
    [ "$(sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env")" = new-image ]
    [ "$(sed -n 's/^SECRET_KEY=//p' "$DEPLOY_ROOT/.env")" = new-secret ]
    printf 'requested-env-promoted-before-stop\n' >> "$COMMAND_LOG"
    ;;
esac
printf 'APP_IMAGE=%s docker %s\n' "${APP_IMAGE-unset}" "$*" >> "$COMMAND_LOG"
if [ "$APPLY_SCENARIO" = worker-removal-failure ] && \
   case " $* " in *" compose "*" --profile worker rm -sf worker "*) true ;; *) false ;; esac; then
  exit 1
fi
if [ "$APPLY_SCENARIO" = worker-recovery ] && \
   [ "${APP_IMAGE-unset}" = unset ] && \
   case " $* " in *" compose "*" up -d --remove-orphans "*) true ;; *) false ;; esac; then
  [ "${PHOTO_PROCESSING_WORKER_TOKEN-unset}" = unset ]
  [ "${PHOTO_WORKER_BUILD-unset}" = unset ]
  [ "${PHOTO_WORKER_LEASE_SECONDS-unset}" = unset ]
  [ "${DB_NAME-unset}" = unset ]
  [ "${PUBLIC_DOMAIN-unset}" = unset ]
  [ "$(sed -n 's/^PHOTO_PROCESSING_WORKER_TOKEN=//p' "$compose_env_file")" = old-worker-token ]
  [ "$(sed -n 's/^PHOTO_WORKER_BUILD=//p' "$compose_env_file")" = old-capture-metadata ]
  [ "$(sed -n 's/^PHOTO_WORKER_LEASE_SECONDS=//p' "$compose_env_file")" = 90 ]
  [ "$(sed -n 's/^DB_NAME=//p' "$compose_env_file")" = old-app ]
  [ "$(sed -n 's/^PUBLIC_DOMAIN=//p' "$compose_env_file")" = old.example ]
  printf 'recovery-compose-uses-restored-environment\n' >> "$COMMAND_LOG"
fi
if [ "$APPLY_SCENARIO" = worker-recovery-disabled ] && \
   [ "${APP_IMAGE-unset}" = unset ] && \
   case " $* " in *" compose "*" --profile worker rm -sf worker "*) true ;; *) false ;; esac; then
  [ "$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$compose_env_file")" = False ]
  printf 'recovery-removes-worker-from-restored-disabled-environment\n' >> "$COMMAND_LOG"
fi
if [ "$APPLY_SCENARIO" = compose-failure ] && \
   [ "${APP_IMAGE-unset}" = new-image ] && \
   case " $* " in *" compose "*" up -d --remove-orphans "*) true ;; *) false ;; esac; then
  exit 1
fi
if [ "$APPLY_SCENARIO" = recovery-failure ] && \
   case " $* " in *" compose "*" up -d --remove-orphans "*) true ;; *) false ;; esac; then
  exit 1
fi
case " $* " in
  *" compose "*" pull "*) [ "$APPLY_SCENARIO" != pull-failure ] ;;
  *" compose "*" ps -q web "*) printf 'web-id\n' ;;
  *" compose "*" ps -q worker "*)
    [ "$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$DEPLOY_ROOT/.env")" = True ] || exit 0
    worker_replicas="$(sed -n 's/^PHOTO_WORKER_REPLICAS=//p' "$DEPLOY_ROOT/.env" | head -n 1)"
    case "$APPLY_SCENARIO" in
      worker-second-missing)
        printf 'worker-first\n'
        ;;
      *)
        printf 'worker-first\n'
        if [ "$worker_replicas" = 2 ]; then
          printf 'worker-second\n'
        fi
        ;;
    esac
    ;;
  *" inspect "*" web-id "*) sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env" ;;
  *" inspect "*" worker-first "*)
    if [ "$APPLY_SCENARIO" = worker-crash-loop ]; then
      case "$*" in
        *OOMKilled*) printf 'true true false 3\n' ;;
        *) printf 'true true 3\n' ;;
      esac
    else
      case "$*" in
        *OOMKilled*) printf 'true false false 0\n' ;;
        *) printf 'true false 0\n' ;;
      esac
    fi
    ;;
  *" inspect "*" worker-second "*)
    if [ "$APPLY_SCENARIO" = worker-second-restarting ]; then
      case "$*" in
        *OOMKilled*) printf 'true true false 1\n' ;;
        *) printf 'true true 1\n' ;;
      esac
    else
      case "$*" in
        *OOMKilled*) printf 'true false false 0\n' ;;
        *) printf 'true false 0\n' ;;
      esac
    fi
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "curl",
        """
printf 'curl %s\n' "$*" >> "$COMMAND_LOG"
if [ "$APPLY_SCENARIO" = health-failure ] || \
   [ "$APPLY_SCENARIO" = worker-recovery ] || \
   [ "$APPLY_SCENARIO" = worker-recovery-disabled ] || \
   [ "$APPLY_SCENARIO" = fresh-first-health-failure ]; then
  [ "$(sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env")" = old-image ]
fi
""",
    )
    _write_executable(fake_bin / "sleep", ":")
    _write_executable(
        fake_bin / "crontab",
        """
if [ "${1-}" = -l ]; then
  exit 1
fi
printf 'crontab %s\n' "$*" >> "$COMMAND_LOG"
""",
    )
    _write_executable(
        fake_bin / "mv",
        """
printf 'mv %s\n' "$*" >> "$COMMAND_LOG"
source_path="${1-}"
target_path="${2-}"
case "$source_path:$target_path" in
  *"/.env.requested."*":$DEPLOY_ROOT/.env")
    case "$APPLY_SCENARIO" in
      promotion-rename-failure)
        exit 1
        ;;
      promotion-term|promotion-hup)
        /bin/mv "$@"
        if [ "$APPLY_SCENARIO" = promotion-term ]; then
          kill -TERM "$PPID"
        else
          kill -HUP "$PPID"
        fi
        exit 0
        ;;
    esac
    ;;
esac
case "$*" in
  *"/deployed-image")
    case "$source_path" in
      *"/.deployed-image.previous."*) ;;
      *)
        [ "$APPLY_SCENARIO" != marker-failure ] && \
          [ "$APPLY_SCENARIO" != fresh-first-marker-failure ] || exit 1
        ;;
    esac
    ;;
esac
/bin/mv "$@"
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(tmp_path / "apply.log"),
        "PREVIOUS_ENV_EXPECTED": str(tmp_path / "previous-env.expected"),
        "GALLERY_PREFLIGHT_HARNESS": str(gallery_preflight_harness),
        "APPLY_SCENARIO": scenario,
        "DEPLOYMENT_TARGET": target,
        "DEPLOY_ROOT": str(tmp_path),
        "COMPOSE_PROJECT_NAME": f"photo-{target}",
        "APP_IMAGE": "new-image",
        "SECRET_KEY": "new-secret",
        "EXPECTED_REQUESTED_SECRET": "new-secret",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "localhost",
        "GUNICORN_WORKERS": "5",
        "GUNICORN_THREADS": "2",
        "GUNICORN_TIMEOUT": "180",
        "GUNICORN_MAX_REQUESTS": "1000",
        "GUNICORN_MAX_REQUESTS_JITTER": "100",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "password",
        "PHOTO_UPLOAD_ENABLED": "False",
        "PRIVATE_MEDIA_S3_BUCKET": "requested-private-bucket",
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "requested-private-access",
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "requested-private-secret",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": "",
        "LETSENCRYPT_EMAIL": "ops@example.com",
    }


def _apply_log(tmp_path: Path) -> list[str]:
    return (tmp_path / "apply.log").read_text(encoding="utf-8").splitlines()


SUCCESS_PHASES = [
    "validate",
    "snapshot",
    "candidate-pull",
    "private-media-preflight",
    "migration-preflight",
    "observability-preflight",
    "observability-reconcile",
    "certificate",
    "compose-reconcile",
    "local-health",
    "worker-health",
    "public-health",
    "observability-verify",
    "commit",
]


def _deployment_markers(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("DEPLOY_")]


def _env_metadata(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mtime_ns,
    )


def _assert_no_env_temporary_files(tmp_path: Path) -> None:
    assert list(tmp_path.glob(".env.*")) == []


def test_apply_propagates_private_media_read_settings(tmp_path: Path, fake_bin: Path) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PRIVATE_MEDIA_S3_BUCKET": "private-gallery",
            "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "gallery-access",
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "gallery-secret",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "PRIVATE_MEDIA_S3_BUCKET=private-gallery" in deployed_env
    assert "PRIVATE_MEDIA_S3_ACCESS_KEY_ID=gallery-access" in deployed_env
    assert "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=gallery-secret" in deployed_env


def test_apply_persists_the_bounded_gunicorn_profile(tmp_path: Path, fake_bin: Path) -> None:
    """The candidate environment must carry the web process bound into the container."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "1000",
            "GUNICORN_MAX_REQUESTS_JITTER": "100",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "GUNICORN_WORKERS=5" in deployed_env
    assert "GUNICORN_THREADS=2" in deployed_env
    assert "GUNICORN_TIMEOUT=180" in deployed_env
    assert "GUNICORN_MAX_REQUESTS=1000" in deployed_env
    assert "GUNICORN_MAX_REQUESTS_JITTER=100" in deployed_env


@pytest.mark.parametrize(
    ("name", "value"),
    [("GUNICORN_WORKERS", "4"), ("GUNICORN_TIMEOUT", "0")],
)
def test_apply_rejects_an_unsafe_gunicorn_profile_before_mutation(
    tmp_path: Path, fake_bin: Path, name: str, value: str
) -> None:
    """A wrong process bound must not replace the live deployment environment."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "1000",
            "GUNICORN_MAX_REQUESTS_JITTER": "100",
            name: value,
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "GUNICORN_" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize(
    ("configuration", "message"),
    [("missing-secret-key", "Set SECRET_KEY"), ("invalid-worker-replicas", "must be 1 or 2")],
)
def test_validate_failure_emits_one_sanitized_result_before_any_mutation(
    tmp_path: Path, fake_bin: Path, configuration: str, message: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "DB_PASSWORD": "validate-db-secret-must-not-appear",
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "validate-object-secret-must-not-appear",
        }
    )
    if configuration == "missing-secret-key":
        env["SECRET_KEY"] = ""
    else:
        env["PHOTO_WORKER_REPLICAS"] = "3"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert message in result.stderr
    assert _deployment_markers(result) == [
        "DEPLOY_PHASE=validate",
        "DEPLOY_RESULT=failure phase=validate rollback=not-needed",
    ]
    output = f"{result.stdout}\n{result.stderr}"
    assert "validate-db-secret-must-not-appear" not in output
    assert "validate-object-secret-must-not-appear" not in output
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_entrypoint_runs_gunicorn_with_the_bounded_profile(tmp_path: Path, fake_bin: Path) -> None:
    """The running web process must receive finite concurrency and recycling arguments."""
    _write_executable(fake_bin / "python", "exit 0")
    _write_executable(fake_bin / "gunicorn", 'printf "%s\\n" "$*" > "$GUNICORN_LOG"')

    result = subprocess.run(
        ["sh", ROOT / "src/backend/entrypoint.sh"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GUNICORN_LOG": str(tmp_path / "gunicorn.log"),
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "1000",
            "GUNICORN_MAX_REQUESTS_JITTER": "100",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gunicorn.log").read_text(encoding="utf-8") == (
        "config.wsgi:application --config python:config.gunicorn --bind 0.0.0.0:8000 "
        "--workers 5 --threads 2 "
        "--timeout 180 --max-requests 1000 --max-requests-jitter 100\n"
    )


def test_entrypoint_recreates_the_shared_multiprocess_directory_before_gunicorn(
    tmp_path: Path, fake_bin: Path
) -> None:
    _write_executable(fake_bin / "python", "exit 0")
    _write_executable(fake_bin / "rm", 'printf "rm %s\\n" "$*" >> "$COMMAND_LOG"')
    _write_executable(fake_bin / "mkdir", 'printf "mkdir %s\\n" "$*" >> "$COMMAND_LOG"')
    _write_executable(
        fake_bin / "gunicorn",
        'printf "multiproc=%s args=%s\\n" "$PROMETHEUS_MULTIPROC_DIR" "$*" >> "$COMMAND_LOG"',
    )

    result = subprocess.run(
        ["sh", ROOT / "src/backend/entrypoint.sh"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(tmp_path / "commands.log"),
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "1000",
            "GUNICORN_MAX_REQUESTS_JITTER": "100",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "commands.log").read_text(encoding="utf-8").splitlines() == [
        "rm -rf /tmp/prometheus_multiproc",
        "mkdir -p /tmp/prometheus_multiproc",
        (
            "multiproc=/tmp/prometheus_multiproc args=config.wsgi:application --config "
            "python:config.gunicorn --bind 0.0.0.0:8000 --workers 5 --threads 2 --timeout 180 "
            "--max-requests 1000 --max-requests-jitter 100"
        ),
    ]


def test_entrypoint_starts_gunicorn_when_multiprocess_directory_cleanup_fails(
    tmp_path: Path, fake_bin: Path
) -> None:
    _write_executable(fake_bin / "python", "exit 0")
    _write_executable(fake_bin / "rm", "exit 1")
    _write_executable(
        fake_bin / "gunicorn",
        'printf "multiproc=%s\\n" "${PROMETHEUS_MULTIPROC_DIR-}" > "$GUNICORN_LOG"',
    )

    result = subprocess.run(
        ["sh", ROOT / "src/backend/entrypoint.sh"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GUNICORN_LOG": str(tmp_path / "gunicorn.log"),
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "1000",
            "GUNICORN_MAX_REQUESTS_JITTER": "100",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gunicorn.log").read_text(encoding="utf-8") == "multiproc=\n"


def test_disabled_processing_persists_defaults_without_the_worker_profile(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A normal deployment must remove a stale worker without trying to start one."""
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "PHOTO_PROCESSING_ENABLED=False" in deployed_env
    assert "PHOTO_PROCESSING_PREVIEW_ENABLED=False" in deployed_env
    assert "PHOTO_PROCESSING_FACE_ENABLED=False" in deployed_env
    assert "PHOTO_WORKER_REPLICAS=1" in deployed_env
    assert "WORKER_IMAGE=" in deployed_env
    assert "PHOTO_PROCESSING_WORKER_TOKEN=" in deployed_env
    assert "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=120" in deployed_env
    assert "PHOTO_PROCESSING_MAX_REQUEST_BYTES=131072" in deployed_env
    assert "PHOTO_WORKER_BUILD=capture-metadata-v1" in deployed_env
    assert "PHOTO_WORKER_LEASE_SECONDS=120" in deployed_env
    assert (
        "PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/1,1/face_embedding/1,"
        "2/generate_preview/1,2/face_embedding/2" in deployed_env
    )
    assert (
        "PHOTO_WORKER_PROCESSOR_TYPES=selfie_query,face_embedding,capture_metadata,"
        "generate_preview" in deployed_env
    )
    assert "ALLOWED_HOSTS=localhost,web,findme-photo.ru" in deployed_env
    commands = _apply_log(tmp_path)
    assert any("--profile worker rm -sf worker" in command for command in commands)
    assert not any("--profile worker up" in command for command in commands)


def test_disabled_processing_removes_a_previously_running_profiled_worker(
    tmp_path: Path, fake_bin: Path
) -> None:
    """Disabling processing must remove a worker started by the prior profile-enabled rollout."""
    previous_env = PREVIOUS_ENV + (
        b"WORKER_IMAGE=old-worker-image\n"
        b"PHOTO_PROCESSING_ENABLED=True\n"
        b"PHOTO_PROCESSING_WORKER_TOKEN=old-worker-token\n"
        b"PHOTO_WORKER_REPLICAS=2\n"
    )
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert any("--profile worker rm -sf worker" in command for command in _apply_log(tmp_path))


def test_disabled_processing_fails_when_stale_worker_removal_fails(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A failed worker removal cannot be hidden by bringing up only the web stack."""
    env = _apply_env(tmp_path, fake_bin, scenario="worker-removal-failure")

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    commands = _apply_log(tmp_path)
    assert any("--profile worker rm -sf worker" in command for command in commands)
    assert not any(" up -d --remove-orphans" in command for command in commands)


def test_disabled_selfie_rollback_replaces_malformed_dormant_overrides_with_safe_values(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "SELFIE_SEARCH_ENABLED": "False",
            "SELFIE_SEARCH_MAX_UPLOAD_BYTES": "not-a-number",
            "SELFIE_SEARCH_MAX_PIXELS": "also-not-a-number",
            "SELFIE_SEARCH_EMBEDDING_MODEL": "different-model",
            "SELFIE_SEARCH_TEMPORARY_PREFIX": "originals/",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SELFIE_SEARCH_ENABLED=False" in deployed_env
    assert "SELFIE_SEARCH_MAX_UPLOAD_BYTES=20971520" in deployed_env
    assert "SELFIE_SEARCH_MAX_PIXELS=25000000" in deployed_env
    assert "SELFIE_SEARCH_EMBEDDING_MODEL=sface" in deployed_env
    assert "SELFIE_SEARCH_TEMPORARY_PREFIX=selfie-search/" in deployed_env


def test_enabled_processing_pulls_and_reconciles_the_worker_profile(
    tmp_path: Path, fake_bin: Path
) -> None:
    """Enabling processing must deploy the immutable worker beside the web service."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token-must-not-be-logged",
            "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS": "240",
            "PHOTO_PROCESSING_MAX_REQUEST_BYTES": "32768",
            "PHOTO_WORKER_BUILD": "capture-metadata-v2",
            "PHOTO_WORKER_LEASE_SECONDS": "180",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "WORKER_IMAGE=worker-image" in deployed_env
    assert "PHOTO_PROCESSING_ENABLED=True" in deployed_env
    assert "PHOTO_PROCESSING_WORKER_TOKEN=worker-token-must-not-be-logged" in deployed_env
    assert "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=240" in deployed_env
    assert "PHOTO_PROCESSING_MAX_REQUEST_BYTES=32768" in deployed_env
    assert "PHOTO_WORKER_BUILD=capture-metadata-v2" in deployed_env
    assert "PHOTO_WORKER_LEASE_SECONDS=180" in deployed_env
    commands = _apply_log(tmp_path)
    assert any("--profile worker pull" in command for command in commands)
    assert any("--profile worker up -d --remove-orphans" in command for command in commands)
    assert "worker-token-must-not-be-logged" not in result.stdout
    assert "worker-token-must-not-be-logged" not in result.stderr
    assert "worker-token-must-not-be-logged" not in "\n".join(commands)


def test_enabled_processing_reconciles_two_requested_worker_replicas(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A requested pair must be persisted and brought up as a pair."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_WORKER_REPLICAS": "2",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert "PHOTO_WORKER_REPLICAS=2" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert any(
        "--profile worker up -d --remove-orphans --scale worker=2" in command
        for command in _apply_log(tmp_path)
    )


@pytest.mark.parametrize("scenario", ("worker-second-missing", "worker-second-restarting"))
def test_two_worker_deployment_rejects_a_missing_or_restarting_replica(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    """One healthy worker cannot make a two-worker rollout successful."""
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_WORKER_REPLICAS": "2",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "worker runtime verification" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV


def test_preview_first_activation_accepts_and_persists_all_worker_identities(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The complete worker contract must reach the deployed environment unchanged."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token-must-not-be-logged",
            "PHOTO_PROCESSING_PREVIEW_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": (
                "1/selfie_query/1,1/capture_metadata/1,1/face_embedding/1,"
                "2/generate_preview/1,2/face_embedding/2,3/face_embedding_benchmark/1"
            ),
            "PHOTO_WORKER_PROCESSOR_TYPES": (
                "selfie_query,face_embedding,capture_metadata,generate_preview"
            ),
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "PHOTO_PROCESSING_PREVIEW_ENABLED=True" in deployed_env
    assert "PHOTO_PROCESSING_FACE_ENABLED=True" in deployed_env
    assert (
        "PHOTO_WORKER_PROCESSOR_IDENTITIES=1/selfie_query/1,1/capture_metadata/1,"
        "1/face_embedding/1,2/generate_preview/1,2/face_embedding/2,"
        "3/face_embedding_benchmark/1" in deployed_env
    )


@pytest.mark.parametrize(
    "identities",
    [
        "1/capture_metadata/1,2/generate_preview/1,2/face_embedding/2,9/bogus/9",
        "1/capture_metadata/1,2/generate_preview/1,2/generate_preview/1,2/face_embedding/2",
        "1/capture_metadata/1, 2/generate_preview/1,2/face_embedding/2",
        "1/capture_metadata/1,",
    ],
)
def test_deployment_rejects_worker_identity_lists_the_worker_would_not_accept(
    tmp_path: Path, fake_bin: Path, identities: str
) -> None:
    """Deployment must reject unknown, duplicate, or whitespace-bearing identities pre-mutation."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": identities,
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "PHOTO_WORKER_PROCESSOR_IDENTITIES must be a unique ordered list" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_enabled_processing_rejects_a_worker_that_is_crash_looping_after_compose_up(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A healthy web container cannot make a restarting worker deployment successful."""
    env = _apply_env(tmp_path, fake_bin, scenario="worker-crash-loop")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "worker runtime verification" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert any(
        "--profile worker" in command and "logs --tail=100 worker" in command
        for command in _apply_log(tmp_path)
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"PHOTO_PROCESSING_PREVIEW_ENABLED": "True"},
            "PHOTO_PROCESSING_PREVIEW_ENABLED requires PHOTO_PROCESSING_ENABLED=True",
        ),
    ],
)
def test_preview_first_activation_rejects_partial_or_implicit_configuration(
    tmp_path: Path, fake_bin: Path, overrides: dict[str, str], message: str
) -> None:
    """Preview and face work must be a conscious operator activation, not an image side effect."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(overrides)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert message in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV


@pytest.mark.parametrize(
    "missing_identity",
    (
        "1/capture_metadata/1",
        "1/face_embedding/1",
        "2/generate_preview/1",
        "2/face_embedding/2",
    ),
)
def test_preview_activation_requires_every_approved_photo_identity_before_mutation(
    tmp_path: Path, fake_bin: Path, missing_identity: str
) -> None:
    required_identities = (
        "1/capture_metadata/1",
        "1/face_embedding/1",
        "2/generate_preview/1",
        "2/face_embedding/2",
    )
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_PROCESSING_PREVIEW_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": ",".join(
                identity for identity in required_identities if identity != missing_identity
            ),
            "PHOTO_WORKER_PROCESSOR_TYPES": (
                "selfie_query,face_embedding,capture_metadata,generate_preview"
            ),
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert f"PHOTO_WORKER_PROCESSOR_IDENTITIES must include {missing_identity}" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"PHOTO_PROCESSING_ENABLED": "true"}, "PHOTO_PROCESSING_ENABLED must be True or False"),
        ({"PHOTO_WORKER_REPLICAS": "3"}, "PHOTO_WORKER_REPLICAS must be 1 or 2"),
        (
            {"PHOTO_PROCESSING_FACE_ENABLED": "true"},
            "PHOTO_PROCESSING_FACE_ENABLED must be True or False",
        ),
        ({"SELFIE_SEARCH_ENABLED": "true"}, "SELFIE_SEARCH_ENABLED must be True or False"),
        (
            {"PHOTO_WORKER_PROCESSOR_TYPES": "capture_metadata,selfie_query"},
            (
                "PHOTO_WORKER_PROCESSOR_TYPES must be "
                "selfie_query,face_embedding,capture_metadata,generate_preview"
            ),
        ),
        (
            {"PHOTO_PROCESSING_ENABLED": "True"},
            "Set WORKER_IMAGE",
        ),
        (
            {
                "PHOTO_PROCESSING_ENABLED": "True",
                "WORKER_IMAGE": "worker-image",
            },
            "Set PHOTO_PROCESSING_WORKER_TOKEN",
        ),
    ],
)
def test_processing_activation_requires_exact_valid_configuration(
    tmp_path: Path,
    fake_bin: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    """Invalid activation never changes the live deployment environment."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(overrides)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert message in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_failed_worker_deployment_restores_the_complete_previous_environment_and_profile(
    tmp_path: Path, fake_bin: Path
) -> None:
    """Recovery must restore all old settings and the old worker-enabled service pair."""
    previous_env = PREVIOUS_ENV + (
        b"WORKER_IMAGE=old-worker-image\n"
        b"PHOTO_PROCESSING_ENABLED=True\n"
        b"PHOTO_PROCESSING_WORKER_TOKEN=old-worker-token\n"
        b"PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=120\n"
        b"PHOTO_PROCESSING_MAX_REQUEST_BYTES=131072\n"
        b"PHOTO_WORKER_BUILD=old-capture-metadata\n"
        b"PHOTO_WORKER_LEASE_SECONDS=90\n"
        b"PHOTO_WORKER_REPLICAS=2\n"
    )
    env = _apply_env(tmp_path, fake_bin, scenario="worker-recovery")
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token-must-not-be-logged",
            "PHOTO_WORKER_BUILD": "candidate-capture-metadata",
            "PHOTO_WORKER_LEASE_SECONDS": "180",
            "PHOTO_WORKER_REPLICAS": "1",
            "DB_NAME": "candidate-app",
            "PUBLIC_DOMAIN": "candidate.example",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == previous_env
    commands = _apply_log(tmp_path)
    assert (
        sum(
            "--profile worker up -d --remove-orphans --scale worker=1" in command
            for command in commands
        )
        == 1
    )
    assert (
        sum(
            "--profile worker up -d --remove-orphans --scale worker=2" in command
            for command in commands
        )
        == 1
    )
    assert "recovery-compose-uses-restored-environment" in commands
    assert "worker-token-must-not-be-logged" not in result.stdout
    assert "worker-token-must-not-be-logged" not in result.stderr
    assert "worker-token-must-not-be-logged" not in "\n".join(commands)


def test_failed_worker_rollout_removes_the_candidate_worker_when_previous_deployment_is_disabled(
    tmp_path: Path, fake_bin: Path
) -> None:
    """Recovery to a disabled deployment cannot leave the candidate profiled worker behind."""
    env = _apply_env(tmp_path, fake_bin, scenario="worker-recovery-disabled")
    previous_env = PREVIOUS_ENV + b"PHOTO_PROCESSING_ENABLED=False\n"
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_WORKER_REPLICAS": "2",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    commands = _apply_log(tmp_path)
    assert any("--profile worker rm -sf worker" in command for command in commands)
    assert "recovery-removes-worker-from-restored-disabled-environment" in commands


def test_candidate_private_media_preflight_skips_when_no_eligible_photo(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    assert "gallery-private-media-preflight-skipped:no-eligible-photo\n" in result.stdout
    assert "Removed upload cleanup schedule.\n" in result.stdout
    assert _deployment_markers(result) == [
        *(f"DEPLOY_PHASE={phase}" for phase in SUCCESS_PHASES),
        "DEPLOY_RESULT=success phase=commit rollback=not-needed",
    ]
    assert result.stderr == "docker compose up exit status: 0\n"
    commands = _apply_log(tmp_path)
    assert "preflight-filter eligible-private-photo" in commands
    assert "preflight-order-by id" in commands
    assert "preflight-first" in commands
    assert not any(command.startswith("preflight-storage") for command in commands)
    assert not any(command.startswith("preflight-open") for command in commands)
    assert not any(command.startswith("preflight-read") for command in commands)


def test_fresh_first_deployment_skips_orm_gate_and_completes_normal_flow(
    tmp_path: Path,
    fake_bin: Path,
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="fresh-first-deployment")
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        (tmp_path / name).unlink()

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert "gallery-private-media-preflight-skipped:no-existing-deployment\n" in result.stdout
    assert "migration-preflight-skipped:no-established-deployment\n" in result.stdout
    assert "Removed upload cleanup schedule.\n" in result.stdout
    assert _deployment_markers(result) == [
        *(f"DEPLOY_PHASE={phase}" for phase in SUCCESS_PHASES),
        "DEPLOY_RESULT=success phase=commit rollback=not-needed",
    ]
    assert result.stderr == "docker compose up exit status: 0\n"
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("APP_IMAGE=new-image\n")
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "new-image\n"
    assert (tmp_path / "deployment-target").read_text(encoding="utf-8") == "production\n"
    assert (tmp_path / "compose-project-name").read_text(encoding="utf-8") == ("photo-production\n")
    commands = _apply_log(tmp_path)
    assert commands.count("volume-inspect photo-production_pgdata") == 1
    candidate_pull = next(index for index, command in enumerate(commands) if " pull web" in command)
    promotion = next(
        index
        for index, command in enumerate(commands)
        if "/.env.requested." in command and command.endswith(f" {tmp_path}/.env")
    )
    stop_nginx = next(index for index, command in enumerate(commands) if " stop nginx" in command)
    assert candidate_pull < promotion < stop_nginx
    assert not any("manage.py shell --no-imports" in command for command in commands)
    assert not any("candidate-migration-history" in command for command in commands)
    assert "candidate-migration-plan" not in commands
    assert "unexpected-fresh-migration-history" not in commands
    assert not any(command.startswith("preflight-") for command in commands)
    _assert_no_env_temporary_files(tmp_path)


def test_retained_postgres_volume_alone_forces_migration_preflight(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="migration-history-database-unavailable")
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        (tmp_path / name).unlink()
    env["EXPECT_CANONICAL_ENV"] = "absent"
    volume_state = tmp_path / ".docker-volume-photo-production_pgdata"
    volume_state.write_text("retained\n", encoding="utf-8")

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Candidate migration preflight failed" in result.stderr
    assert "migration-preflight-skipped:no-established-deployment" not in result.stdout
    assert _deployment_markers(result) == [
        "DEPLOY_PHASE=validate",
        "DEPLOY_PHASE=snapshot",
        "DEPLOY_PHASE=candidate-pull",
        "DEPLOY_PHASE=private-media-preflight",
        "DEPLOY_PHASE=migration-preflight",
        "DEPLOY_RESULT=failure phase=migration-preflight rollback=not-needed",
    ]
    commands = _apply_log(tmp_path)
    assert commands.count("volume-inspect photo-production_pgdata") == 1
    assert (
        commands.index("volume-inspect photo-production_pgdata")
        < next(index for index, command in enumerate(commands) if " pull web" in command)
        < commands.index("candidate-migration-history database-unavailable")
    )
    assert "candidate-migration-plan" not in commands
    assert volume_state.read_text(encoding="utf-8") == "retained\n"
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        assert not (tmp_path / name).exists()
    assert not any("observability-" in command for command in commands)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --remove-orphans" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    _assert_no_env_temporary_files(tmp_path)


def test_postgres_volume_inspection_error_fails_safely_before_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="volume-inspection-error")
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        (tmp_path / name).unlink()
    env["EXPECT_CANONICAL_ENV"] = "absent"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "PostgreSQL deployment volume inspection failed" in result.stderr
    assert "docker socket failure raw detail must stay hidden" not in result.stdout
    assert "docker socket failure raw detail must stay hidden" not in result.stderr
    assert _deployment_markers(result) == [
        "DEPLOY_PHASE=validate",
        "DEPLOY_PHASE=snapshot",
        "DEPLOY_RESULT=failure phase=snapshot rollback=not-needed",
    ]
    assert _apply_log(tmp_path) == ["volume-inspect photo-production_pgdata"]
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        assert not (tmp_path / name).exists()
    _assert_no_env_temporary_files(tmp_path)


def test_failed_first_deployment_restores_the_no_env_state(tmp_path: Path, fake_bin: Path) -> None:
    """A failed initial rollout leaves neither a candidate environment nor worker service."""
    env = _apply_env(tmp_path, fake_bin, scenario="fresh-first-health-failure")
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        (tmp_path / name).unlink()

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "deployed-image").exists()
    assert not (tmp_path / "deployment-target").exists()
    assert not (tmp_path / "compose-project-name").exists()
    commands = _apply_log(tmp_path)
    assert any(" down --remove-orphans" in command for command in commands)
    assert not any("--profile worker" in command for command in commands)
    _assert_no_env_temporary_files(tmp_path)


def test_failed_first_deployment_after_metadata_markers_restores_no_env_state(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A late failed initial rollout removes the metadata that it created before failing."""
    env = _apply_env(tmp_path, fake_bin, scenario="fresh-first-marker-failure")
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        (tmp_path / name).unlink()

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    for name in (".env", "deployed-image", "deployment-target", "compose-project-name"):
        assert not (tmp_path / name).exists()
    _assert_no_env_temporary_files(tmp_path)


def test_candidate_private_media_preflight_reads_when_photo_exists(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-success"),
    )

    assert result.returncode == 0, result.stderr
    assert "gallery-private-media-preflight-ok\n" in result.stdout
    assert "Removed upload cleanup schedule.\n" in result.stdout
    assert result.stderr == "docker compose up exit status: 0\n"
    commands = _apply_log(tmp_path)
    assert commands.count("preflight-storage-init") == 1
    assert commands.count("preflight-open originals/eligible-photo") == 1
    assert commands.count("preflight-read 1") == 1
    assert commands.count("preflight-close") == 1


def test_candidate_private_media_preflight_runs_before_service_switch(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-success"),
    )

    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    candidate_pull = next(
        index
        for index, command in enumerate(commands)
        if " pull web" in command and "APP_IMAGE=new-image" in command
    )
    candidate_command = next(
        command
        for command in commands
        if " run --rm --no-deps -T --entrypoint python web " in command
    )
    candidate_run = commands.index(candidate_command)
    assert f"--env-file {tmp_path}/.env.requested." in candidate_command
    assert "manage.py shell --no-imports -c <gallery_media_preflight>" in candidate_command
    stop_nginx = next(index for index, command in enumerate(commands) if " stop nginx" in command)
    candidate_up = next(
        index
        for index, command in enumerate(commands)
        if " up -d --remove-orphans" in command and "APP_IMAGE=new-image" in command
    )
    assert candidate_pull < candidate_run < stop_nginx < candidate_up


@pytest.mark.parametrize(
    "scenario",
    [
        "private-media-failure",
        "private-media-config-failure",
        "private-media-db-failure",
    ],
)
def test_failed_candidate_private_media_preflight_leaves_canonical_env_untouched(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    previous_metadata = _env_metadata(tmp_path / ".env")
    result = _run(
        "deploy/apply-deployment.sh",
        env=env,
    )

    assert result.returncode != 0
    assert _deployment_markers(result)[-1] == (
        "DEPLOY_RESULT=failure phase=private-media-preflight rollback=not-needed"
    )
    assert result.stderr == (
        "Gallery private-media read prerequisite failed\n"
        "Candidate image failed private-media read prerequisite\n"
    )
    assert "Gallery private-media read prerequisite failed" in result.stderr
    assert "private failure detail" not in result.stderr
    assert "private config detail" not in result.stderr
    assert "database unavailable detail" not in result.stderr
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / "deployment-target").read_bytes() == b"old-target\n"
    assert (tmp_path / "compose-project-name").read_bytes() == b"old-project\n"
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert _env_metadata(tmp_path / ".env") == previous_metadata
    commands = _apply_log(tmp_path)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --remove-orphans" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    assert commands.count("candidate-requested-env-with-canonical-untouched") == 2
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize(
    "scenario", ["migration-history-missing", "migration-history-database-unavailable"]
)
def test_failed_candidate_migration_history_stops_before_any_deployment_mutation(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    env.update(
        {
            "SECRET_KEY": "fake-secret-must-not-appear",
            "EXPECTED_REQUESTED_SECRET": "fake-secret-must-not-appear",
            "DB_PASSWORD": "fake-password-must-not-appear",
            "PRIVATE_MEDIA_S3_BUCKET": "fake-object-key-must-not-appear",
            "PUBLIC_DOMAIN": "fake.example/bearer-path-must-not-appear",
        }
    )
    if scenario == "migration-history-missing":
        env.update(
            {
                "CANDIDATE_MIGRATION_LEDGER": "selfie_search.0003_optional_feedback_contact",
                "CANDIDATE_MIGRATION_GRAPH": "picflow.0001_initial",
            }
        )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Candidate migration preflight failed" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / "deployment-target").read_text(encoding="utf-8") == "old-target\n"
    assert (tmp_path / "compose-project-name").read_text(encoding="utf-8") == "old-project\n"
    assert _deployment_markers(result) == [
        "DEPLOY_PHASE=validate",
        "DEPLOY_PHASE=snapshot",
        "DEPLOY_PHASE=candidate-pull",
        "DEPLOY_PHASE=private-media-preflight",
        "DEPLOY_PHASE=migration-preflight",
        "DEPLOY_RESULT=failure phase=migration-preflight rollback=not-needed",
    ]
    commands = _apply_log(tmp_path)
    expected_history = {
        "migration-history-missing": (
            "candidate-migration-history "
            "applied=selfie_search.0003_optional_feedback_contact "
            "candidate=picflow.0001_initial"
        ),
        "migration-history-database-unavailable": (
            "candidate-migration-history database-unavailable"
        ),
    }[scenario]
    assert expected_history in commands
    assert "candidate-migration-env-mode-0600" in commands
    assert "candidate-migration-plan" not in commands
    assert not any("observability-" in command for command in commands)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --remove-orphans" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    output = "\n".join((result.stdout, result.stderr, *commands))
    for secret in (
        "fake-secret-must-not-appear",
        "fake-password-must-not-appear",
        "fake-object-key-must-not-appear",
        "fake.example/bearer-path-must-not-appear",
    ):
        assert secret not in output
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize(
    ("established_signal", "expected_content"),
    [
        (".env", PREVIOUS_ENV),
        ("deployment-target", b"old-target\n"),
        ("compose-project-name", b"old-project\n"),
        ("deployed-image", b"old-image\n"),
    ],
)
def test_each_durable_deployment_signal_alone_requires_migration_preflight(
    tmp_path: Path,
    fake_bin: Path,
    established_signal: str,
    expected_content: bytes,
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="migration-history-database-unavailable")
    durable_signals = (".env", "deployment-target", "compose-project-name", "deployed-image")
    for signal in durable_signals:
        if signal != established_signal:
            (tmp_path / signal).unlink()
    if established_signal != ".env":
        env["EXPECT_CANONICAL_ENV"] = "absent"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Candidate migration preflight failed" in result.stderr
    assert "migration-preflight-skipped:no-established-deployment" not in result.stdout
    assert _deployment_markers(result) == [
        "DEPLOY_PHASE=validate",
        "DEPLOY_PHASE=snapshot",
        "DEPLOY_PHASE=candidate-pull",
        "DEPLOY_PHASE=private-media-preflight",
        "DEPLOY_PHASE=migration-preflight",
        "DEPLOY_RESULT=failure phase=migration-preflight rollback=not-needed",
    ]
    assert (tmp_path / established_signal).read_bytes() == expected_content
    for signal in durable_signals:
        assert (tmp_path / signal).exists() is (signal == established_signal)
    commands = _apply_log(tmp_path)
    assert commands.count("candidate-migration-history database-unavailable") == 1
    assert "candidate-migration-plan" not in commands
    assert not any("observability-" in command for command in commands)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --remove-orphans" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize(
    ("scenario", "rollback"),
    [("compose-failure", "succeeded"), ("recovery-failure", "failed")],
)
def test_post_mutation_compose_failure_reports_the_recovery_outcome(
    tmp_path: Path, fake_bin: Path, scenario: str, rollback: str
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario=scenario),
    )

    assert result.returncode != 0
    assert (
        _deployment_markers(result).count(
            f"DEPLOY_RESULT=failure phase=compose-reconcile rollback={rollback}"
        )
        == 1
    )
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    commands = _apply_log(tmp_path)
    assert "observability-install" in commands
    assert "observability-rollback" in commands
    if rollback == "succeeded":
        assert "Previous application and worker profile reconciled" in result.stderr
    else:
        assert "Previous deployment recovery failed" in result.stderr


def test_candidate_pull_failure_leaves_canonical_env_without_service_reconciliation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="pull-failure")
    previous_metadata = _env_metadata(tmp_path / ".env")

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert _env_metadata(tmp_path / ".env") == previous_metadata
    assert (tmp_path / "deployed-image").read_bytes() == b"old-image\n"
    assert (tmp_path / "deployment-target").read_bytes() == b"old-target\n"
    assert (tmp_path / "compose-project-name").read_bytes() == b"old-project\n"
    commands = _apply_log(tmp_path)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --remove-orphans" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    assert commands.count("candidate-requested-env-with-canonical-untouched") == 1
    _assert_no_env_temporary_files(tmp_path)


def test_workflows_forward_private_media_settings() -> None:
    for relative_path in (
        ".github/workflows/deploy.yml",
        ".github/workflows/promote-production.yml",
    ):
        workflow = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "PRIVATE_MEDIA_S3_BUCKET: ${{ vars.PRIVATE_MEDIA_S3_BUCKET }}" in workflow
        assert (
            "PRIVATE_MEDIA_S3_ACCESS_KEY_ID: "
            "${{ secrets.PRIVATE_MEDIA_S3_ACCESS_KEY_ID }}" in workflow
        )
        assert (
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY: "
            "${{ secrets.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY }}" in workflow
        )
        forwarded = next(
            line
            for line in workflow.splitlines()
            if "envs: APP_IMAGE" in line and "SECRET_KEY" in line
        )
        assert "PRIVATE_MEDIA_S3_BUCKET" in forwarded
        assert "PRIVATE_MEDIA_S3_ACCESS_KEY_ID" in forwarded
        assert "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY" in forwarded


def test_deployment_path_performs_no_iam_mutation(tmp_path: Path, fake_bin: Path) -> None:
    for tool in ("yc", "aws", "s3cmd"):
        _write_executable(
            fake_bin / tool,
            f'printf \'{tool} %s\\n\' "$*" >> "$COMMAND_LOG"\nexit 97',
        )

    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-success"),
    )

    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path)).lower()
    assert "yc " not in commands
    assert "aws " not in commands
    assert "s3cmd " not in commands
    assert "policy" not in commands
    assert " iam " not in commands
    assert "role-binding" not in commands
    assert "bucket-policy" not in commands


def test_staging_apply_activates_https_edge_and_public_checks(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="success", target="staging"),
    )

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert "docker-compose.https.yml" in commands
    assert "docker-compose.staging.yml" not in commands
    assert "stop nginx" in commands
    assert "reconcile-certificate" in commands
    assert "https://findme-photo.ru/health/" in commands
    assert "verify-public-edge" in commands


def test_staging_apply_labels_web_metrics_as_staging(tmp_path: Path, fake_bin: Path) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="success", target="staging"),
    )

    assert result.returncode == 0, result.stderr
    assert "MONITORING_ENVIRONMENT=staging\n" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_apply_success_commits_deployed_image_only_after_checks(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="success"),
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "new-image\n"
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("APP_IMAGE=new-image\n")
    assert (tmp_path / "deployment-target").read_text(encoding="utf-8") == "production\n"
    assert (tmp_path / "compose-project-name").read_text(encoding="utf-8") == ("photo-production\n")
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.count("up -d --remove-orphans") == 1
    assert commands.count("requested-env-promoted-before-stop") == 1
    assert "https://findme-photo.ru/health/" in commands
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize("scenario", ["health-failure", "public-failure"])
def test_apply_failure_restores_previous_image_and_overlay_without_marker_change(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario=scenario),
    )

    assert result.returncode != 0
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert (tmp_path / "deployment-target").read_bytes() == b"old-target\n"
    assert (tmp_path / "compose-project-name").read_bytes() == b"old-project\n"
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.count("up -d --remove-orphans") >= 2


def test_certificate_bootstrap_failure_reconciles_previous_https_edge(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="certificate-failure"),
    )

    assert result.returncode != 0
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.index("stop nginx") < commands.index("up -d --remove-orphans")
    assert "docker-compose.https.yml" in commands


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [("promotion-term", 143), ("promotion-hup", 129)],
)
def test_signal_after_env_promotion_enters_existing_image_only_recovery(
    tmp_path: Path,
    fake_bin: Path,
    scenario: str,
    expected_status: int,
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario=scenario),
    )

    assert result.returncode == expected_status
    assert "Previous application and worker profile reconciled" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert (tmp_path / "deployed-image").read_bytes() == b"old-image\n"
    assert (tmp_path / "deployment-target").read_bytes() == b"old-target\n"
    assert (tmp_path / "compose-project-name").read_bytes() == b"old-project\n"
    commands = _apply_log(tmp_path)
    assert commands.count("candidate-requested-env-with-canonical-untouched") == 4
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert sum(" up -d --remove-orphans" in command for command in commands) == 1
    assert any(
        "APP_IMAGE=unset" in command and " up -d --remove-orphans" in command
        for command in commands
    )
    _assert_no_env_temporary_files(tmp_path)


def test_failed_env_promotion_removes_secret_bearing_requested_temp(
    tmp_path: Path,
    fake_bin: Path,
) -> None:
    requested_secret = "promotion-secret-must-not-persist"
    env = _apply_env(tmp_path, fake_bin, scenario="promotion-rename-failure")
    env["SECRET_KEY"] = requested_secret
    env["EXPECTED_REQUESTED_SECRET"] = requested_secret

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert (tmp_path / "deployed-image").read_bytes() == b"old-image\n"
    assert (tmp_path / "deployment-target").read_bytes() == b"old-target\n"
    assert (tmp_path / "compose-project-name").read_bytes() == b"old-project\n"
    commands = _apply_log(tmp_path)
    assert sum(" up -d --remove-orphans" in command for command in commands) == 1
    assert requested_secret not in result.stdout
    assert requested_secret not in result.stderr
    assert requested_secret not in "\n".join(commands)
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize(
    ("scenario", "expected_reconciliations"),
    [("marker-failure", 2)],
)
def test_unexpected_failure_after_env_mutation_triggers_exit_recovery(
    tmp_path: Path,
    fake_bin: Path,
    scenario: str,
    expected_reconciliations: int,
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario=scenario),
    )

    assert result.returncode != 0
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.count("up -d --remove-orphans") == expected_reconciliations


def test_failed_certificate_renewal_waits_before_next_attempt(
    tmp_path: Path, fake_bin: Path
) -> None:
    log = tmp_path / "renew.log"
    _write_executable(fake_bin / "certbot", 'printf "certbot %s\\n" "$*" >> "$COMMAND_LOG"\nexit 1')
    _write_executable(fake_bin / "sleep", 'printf "sleep %s\\n" "$*" >> "$COMMAND_LOG"\nexit 7')

    result = _run(
        "deploy/certbot/renew-certificates.sh",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
        },
    )

    assert result.returncode == 7
    assert log.read_text(encoding="utf-8").splitlines() == [
        "certbot renew --webroot --webroot-path /var/www/certbot --quiet",
        "sleep 43200",
    ]


def test_feedback_activation_requires_a_confirmed_storage_preflight_before_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    """Feedback must remain disabled if the operator has not confirmed its real-bucket probe."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "SELFIE_SEARCH_ENABLED": "True",
            "PRIVATE_MEDIA_S3_BUCKET": "private-search",
            "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "private-access",
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "private-secret",
            "SELFIE_FEEDBACK_ENABLED": "True",
            "SELFIE_FEEDBACK_S3_BUCKET": "feedback-private",
            "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "feedback-access",
            "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "feedback-secret",
            "SELFIE_FEEDBACK_KMS_KEY_ID": "kms-feedback-key",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_confirmed_feedback_activation_persists_web_only_storage_configuration(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The candidate web environment receives the dedicated bucket, KMS key, and no worker copy."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "SELFIE_SEARCH_ENABLED": "True",
            "PRIVATE_MEDIA_S3_BUCKET": "private-search",
            "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "private-access",
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "private-secret",
            "SELFIE_FEEDBACK_ENABLED": "True",
            "SELFIE_FEEDBACK_S3_BUCKET": "feedback-private",
            "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "feedback-access",
            "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "feedback-secret-must-not-be-logged",
            "SELFIE_FEEDBACK_KMS_KEY_ID": "kms-feedback-key",
            "SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED": "True",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SELFIE_FEEDBACK_ENABLED=True" in deployed_env
    assert "SELFIE_FEEDBACK_S3_BUCKET=feedback-private" in deployed_env
    assert "SELFIE_FEEDBACK_KMS_KEY_ID=kms-feedback-key" in deployed_env
    assert "SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED=True" in deployed_env
    assert "feedback-secret-must-not-be-logged" not in result.stdout
    assert "feedback-secret-must-not-be-logged" not in result.stderr
    assert "feedback-secret-must-not-be-logged" not in "\n".join(_apply_log(tmp_path))


def test_feedback_workflow_forwards_web_credentials_and_keeps_them_out_of_worker_compose_env() -> (
    None
):
    """The workflow is the only feedback-credential ingress; worker never gets them."""
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "SELFIE_FEEDBACK_ENABLED: ${{ vars.SELFIE_FEEDBACK_ENABLED || 'False' }}" in workflow
    assert "SELFIE_FEEDBACK_S3_BUCKET: ${{ vars.SELFIE_FEEDBACK_S3_BUCKET }}" in workflow
    assert (
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID: "
        "${{ secrets.SELFIE_FEEDBACK_S3_ACCESS_KEY_ID }}" in workflow
    )
    assert (
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY: "
        "${{ secrets.SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY }}" in workflow
    )
    assert "SELFIE_FEEDBACK_KMS_KEY_ID: ${{ vars.SELFIE_FEEDBACK_KMS_KEY_ID }}" in workflow
    assert "verify_selfie_feedback_storage" in workflow
    worker_section = compose.split("  worker:\n", maxsplit=1)[1]
    assert "SELFIE_FEEDBACK_" not in worker_section


def _observability_install_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    source = tmp_path / "deploy" / "selfie-observability"
    source.mkdir(parents=True)
    for name in (
        "journald.conf",
        "selfie-search-summary.service",
        "selfie-search-summary.timer",
        "run-daily-summary.sh",
        "summarize.py",
        "root-helper.sh",
    ):
        shutil.copy2(ROOT / "deploy" / "selfie-observability" / name, source / name)
    _write_executable(
        fake_bin / "systemctl",
        """
printf '%s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  "enable --now selfie-search-summary.timer")
    [ "$TIMER_ENABLE_FAILURE" = none ] || exit 1
    ;;
  "disable selfie-search-summary.timer")
    : > "$COMMAND_LOG.disable-attempted"
    if [ "$TIMER_MISSING_COMMAND_FAILURE" = 1 ] && \
       [ ! -e "$SELFIE_OBSERVABILITY_SYSTEMD_DIR/selfie-search-summary.timer" ]; then
      exit 1
    fi
    [ "$TIMER_ROLLBACK_FAILURE" != disable ] || exit 1
    ;;
  "stop selfie-search-summary.timer")
    : > "$COMMAND_LOG.stop-attempted"
    if [ "$TIMER_MISSING_COMMAND_FAILURE" = 1 ] && \
       [ ! -e "$SELFIE_OBSERVABILITY_SYSTEMD_DIR/selfie-search-summary.timer" ]; then
      exit 1
    fi
    [ "$TIMER_ROLLBACK_FAILURE" != stop ] || exit 1
    ;;
  "restart systemd-journald")
    if [ "$ROLLBACK_LATE_FAILURE" = twice ] && \
       [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/timer.enable-attempted" ]; then
      if [ ! -f "$COMMAND_LOG.late-failed-1" ]; then
        : > "$COMMAND_LOG.late-failed-1"
        exit 1
      fi
      if [ ! -f "$COMMAND_LOG.late-failed-2" ]; then
        : > "$COMMAND_LOG.late-failed-2"
        exit 1
      fi
    fi
    ;;
  "is-enabled --quiet selfie-search-summary.timer")
    if [ "$TIMER_ROLLBACK_FAILURE" = disable ] && [ -f "$COMMAND_LOG.disable-attempted" ]; then
      exit 0
    fi
    [ "$TIMER_INITIAL" != disabled ] || exit 1
    ;;
  "is-active --quiet selfie-search-summary.timer")
    if [ "$TIMER_ROLLBACK_FAILURE" = stop ] && [ -f "$COMMAND_LOG.stop-attempted" ]; then
      exit 0
    fi
    [ "$TIMER_INITIAL" != disabled ] || exit 1
    ;;
esac
""",
    )
    _write_executable(fake_bin / "systemd-analyze", ":")
    _write_executable(
        fake_bin / "stat",
        """
case "$*" in *.sh|*.py) printf 'root:root:755\n' ;; *) printf 'root:root:644\n' ;; esac
""",
    )
    _write_executable(
        fake_bin / "install",
        """
args=""
is_dir=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o|-g|-m) shift 2 ;;
    -d) is_dir=1; shift ;;
    *) args="$args '$1'"; shift ;;
  esac
done
if [ "$is_dir" -eq 1 ]; then
  eval "/usr/bin/install -d $args"
else
  eval "/usr/bin/install $args"
fi
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SELFIE_OBSERVABILITY_TEST_MODE": "1",
        "SELFIE_OBSERVABILITY_PACKAGE_DIR": str(source),
        "SELFIE_OBSERVABILITY_STATE_DIR": str(tmp_path / "state"),
        "SELFIE_OBSERVABILITY_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "SELFIE_OBSERVABILITY_JOURNALD_DIR": str(tmp_path / "journald"),
        "SELFIE_OBSERVABILITY_RUNTIME_DIR": str(tmp_path / "runtime"),
        "COMMAND_LOG": str(tmp_path / "systemctl.log"),
        "TIMER_INITIAL": "enabled",
        "TIMER_ROLLBACK_FAILURE": "none",
        "TIMER_ENABLE_FAILURE": "none",
        "TIMER_MISSING_COMMAND_FAILURE": "0",
        "ROLLBACK_LATE_FAILURE": "none",
    }


def test_observability_installer_first_install_noop_and_exact_rollback(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    journald = tmp_path / "journald"
    systemd = tmp_path / "systemd"
    journald.mkdir()
    systemd.mkdir()
    (systemd / "selfie-search-summary.timer").write_text("prior timer\n", encoding="utf-8")
    managed = journald / "60-findme-selfie-observability.conf"
    managed.write_text("prior-managed\n", encoding="utf-8")
    unrelated = systemd / "unrelated.service"
    unrelated.write_text("leave-me\n", encoding="utf-8")

    first = _run("deploy/selfie-observability/root-helper.sh", env=env)
    noop_env = {**env, "SELFIE_OBSERVABILITY_STATE_DIR": str(tmp_path / "noop-state")}
    second = _run("deploy/selfie-observability/root-helper.sh", env=noop_env)
    rollback = subprocess.run(
        ["sh", ROOT / "deploy/selfie-observability/root-helper.sh", "rollback"],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode == second.returncode == rollback.returncode == 0
    assert "SELFIE_OBSERVABILITY_INSTALL_READY" in first.stdout
    assert managed.read_text(encoding="utf-8") == "prior-managed\n"
    assert unrelated.read_text(encoding="utf-8") == "leave-me\n"


def test_observability_installer_rejects_invalid_candidate_before_host_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    (tmp_path / "deploy/selfie-observability/journald.conf").write_text(
        "[Journal]\nStorage=volatile\n", encoding="utf-8"
    )

    before = sorted(
        path.relative_to(tmp_path) for path in tmp_path.rglob("*") if "bin" not in path.parts
    )
    result = _run("deploy/selfie-observability/root-helper.sh", env=env)
    after = sorted(
        path.relative_to(tmp_path) for path in tmp_path.rglob("*") if "bin" not in path.parts
    )

    assert result.returncode != 0
    assert not (tmp_path / "journald/60-findme-selfie-observability.conf").exists()
    assert after == before


def test_observability_installer_second_file_failure_restores_files_and_disabled_timer(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env["TIMER_INITIAL"] = "disabled"
    # Fail the candidate write for the second managed file exactly once.
    install = fake_bin / "install"
    source = install.read_text(encoding="utf-8")
    install.write_text(
        source.replace(
            'if [ "$is_dir" -eq 1 ]; then',
            'case "$args" in *selfie-search-summary.service.candidate*) '
            '[ -f "$COMMAND_LOG.failed" ] || { : > "$COMMAND_LOG.failed"; exit 9; } ;; esac\n'
            'if [ "$is_dir" -eq 1 ]; then',
        ),
        encoding="utf-8",
    )
    journald = tmp_path / "journald"
    systemd = tmp_path / "systemd"
    runtime = tmp_path / "runtime"
    journald.mkdir()
    systemd.mkdir()
    runtime.mkdir()
    (systemd / "selfie-search-summary.timer").write_text("prior timer\n", encoding="utf-8")
    managed = journald / "60-findme-selfie-observability.conf"
    managed.write_text("prior\n", encoding="utf-8")

    result = _run("deploy/selfie-observability/root-helper.sh", env=env)

    assert result.returncode == 9
    assert managed.read_text(encoding="utf-8") == "prior\n"
    assert not (tmp_path / "state").exists()
    commands = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "disable selfie-search-summary.timer" in commands
    assert "stop selfie-search-summary.timer" in commands


def test_observability_first_install_rollback_accepts_absent_timer_unit(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env["TIMER_INITIAL"] = "disabled"
    install = fake_bin / "install"
    install.write_text(
        install.read_text(encoding="utf-8").replace(
            'if [ "$is_dir" -eq 1 ]; then',
            'case "$args" in *selfie-search-summary.service.candidate*) exit 9 ;; esac\n'
            'if [ "$is_dir" -eq 1 ]; then',
        ),
        encoding="utf-8",
    )
    (tmp_path / "journald").mkdir()
    (tmp_path / "systemd").mkdir()
    (tmp_path / "runtime").mkdir()

    result = _run("deploy/selfie-observability/root-helper.sh", env=env)

    assert result.returncode == 9
    assert "SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE" in result.stdout
    assert not (tmp_path / "state").exists()
    commands = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "disable selfie-search-summary.timer" not in commands
    assert "stop selfie-search-summary.timer" not in commands


def test_observability_successful_first_install_rollback_disables_new_timer(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env["TIMER_INITIAL"] = "disabled"
    (tmp_path / "journald").mkdir()
    (tmp_path / "systemd").mkdir()

    installed = _run("deploy/selfie-observability/root-helper.sh", env=env)
    rollback = subprocess.run(
        ["sh", ROOT / "deploy/selfie-observability/root-helper.sh", "rollback"],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert installed.returncode == rollback.returncode == 0
    assert "SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE" in rollback.stdout
    assert not (tmp_path / "state").exists()
    commands = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "disable selfie-search-summary.timer" in commands
    assert "stop selfie-search-summary.timer" in commands


def test_observability_partial_timer_enable_failure_rolls_back_first_install(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env.update({"TIMER_INITIAL": "disabled", "TIMER_ENABLE_FAILURE": "fail"})
    (tmp_path / "journald").mkdir()
    (tmp_path / "systemd").mkdir()

    result = _run("deploy/selfie-observability/root-helper.sh", env=env)

    assert result.returncode != 0
    assert "SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE" in result.stdout
    assert not (tmp_path / "state").exists()
    commands = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "enable --now selfie-search-summary.timer" in commands
    assert "disable selfie-search-summary.timer" in commands
    assert "stop selfie-search-summary.timer" in commands


def test_observability_first_install_rollback_retry_accepts_already_removed_timer(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env.update(
        {
            "TIMER_INITIAL": "disabled",
            "TIMER_MISSING_COMMAND_FAILURE": "1",
            "ROLLBACK_LATE_FAILURE": "twice",
        }
    )
    (tmp_path / "journald").mkdir()
    (tmp_path / "systemd").mkdir()

    installed = _run("deploy/selfie-observability/root-helper.sh", env=env)
    first = subprocess.run(
        ["sh", ROOT / "deploy/selfie-observability/root-helper.sh", "rollback"],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )
    armed_after_first = (tmp_path / "state/transaction-armed").exists()
    second = subprocess.run(
        ["sh", ROOT / "deploy/selfie-observability/root-helper.sh", "rollback"],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert installed.returncode == 0
    assert first.returncode != 0
    assert armed_after_first
    assert second.returncode == 0
    assert "SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE" in second.stdout
    assert not (tmp_path / "state").exists()


def test_observability_installer_signal_after_first_replacement_restores_prior_file(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    install = fake_bin / "install"
    source = install.read_text(encoding="utf-8")
    install.write_text(
        source.replace(
            'if [ "$is_dir" -eq 1 ]; then',
            'case "$args" in *60-findme-selfie-observability.conf.candidate*) '
            '[ -f "$COMMAND_LOG.signalled" ] || { : > "$COMMAND_LOG.signalled"; '
            'kill -TERM "$PPID"; } ;; esac\n'
            'if [ "$is_dir" -eq 1 ]; then',
        ),
        encoding="utf-8",
    )
    (tmp_path / "journald").mkdir()
    (tmp_path / "systemd").mkdir()
    (tmp_path / "runtime").mkdir()
    managed = tmp_path / "journald/60-findme-selfie-observability.conf"
    managed.write_text("prior\n", encoding="utf-8")

    result = _run("deploy/selfie-observability/root-helper.sh", env=env)

    assert result.returncode == 143
    assert managed.read_text(encoding="utf-8") == "prior\n"
    assert not (tmp_path / "state").exists()
    assert "SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE" in result.stdout


def test_installed_runner_uses_managed_sibling_after_candidate_changes(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    candidate = tmp_path / "deploy/selfie-observability/summarize.py"
    candidate.write_text("print('managed-summary')\n", encoding="utf-8")

    installed = _run("deploy/selfie-observability/root-helper.sh", env=env)
    assert installed.returncode == 0, installed.stderr
    candidate.write_text("print('candidate-summary')\n", encoding="utf-8")
    _write_executable(fake_bin / "journalctl", ":")

    result = subprocess.run(
        ["sh", tmp_path / "runtime/run-daily-summary.sh", "2026-08-03"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(tmp_path),
            "DEPLOYMENT_TARGET": "staging",
            "PYTHON_BIN": os.sys.executable,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "managed-summary\n"


@pytest.mark.parametrize("failed_command", ["disable", "stop"])
def test_observability_rollback_never_claims_complete_when_timer_state_remains_wrong(
    tmp_path: Path, fake_bin: Path, failed_command: str
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env.update({"TIMER_INITIAL": "disabled", "TIMER_ROLLBACK_FAILURE": failed_command})
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd/selfie-search-summary.timer").write_text("prior timer\n", encoding="utf-8")
    install = fake_bin / "install"
    install.write_text(
        install.read_text(encoding="utf-8").replace(
            'if [ "$is_dir" -eq 1 ]; then',
            'case "$args" in *selfie-search-summary.service.candidate*) exit 9 ;; esac\n'
            'if [ "$is_dir" -eq 1 ]; then',
        ),
        encoding="utf-8",
    )

    result = _run("deploy/selfie-observability/root-helper.sh", env=env)

    assert result.returncode == 9
    assert "SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE" not in result.stdout
    assert "SELFIE_OBSERVABILITY_ROLLBACK_FAILED" in result.stderr


@pytest.mark.parametrize(
    ("scenario", "expected_success"),
    [
        ("ok", True),
        ("oldest", True),
        ("disabled", False),
        ("inactive", False),
        ("disk-failure", False),
    ],
)
def test_root_helper_executes_host_verification(
    tmp_path: Path, fake_bin: Path, scenario: str, expected_success: bool
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    journal = tmp_path / "journal"
    journal.mkdir()
    env.update({"SELFIE_OBSERVABILITY_JOURNAL_DIR": str(journal), "VERIFY_SCENARIO": scenario})
    _write_executable(
        fake_bin / "systemd-analyze",
        """
case "$*" in
  *cat-config*) printf 'Storage=persistent\nMaxRetentionSec=14day\nSystemMaxUse=1G\n' ;;
  *) : ;;
esac
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """
case "$*" in
  "is-enabled --quiet selfie-search-summary.timer") [ "$VERIFY_SCENARIO" != disabled ] ;;
  "is-active --quiet selfie-search-summary.timer") [ "$VERIFY_SCENARIO" != inactive ] ;;
  *) : ;;
esac
""",
    )
    _write_executable(
        fake_bin / "journalctl",
        """
case "$*" in
  *--disk-usage*)
    [ "$VERIFY_SCENARIO" != disk-failure ] || exit 1
    printf 'Archived and active journals take up 12.0M in the file system.\n'
    ;;
  *"-o short-unix"*)
    [ "$VERIFY_SCENARIO" != oldest ] || printf '%s\n100.000000 first\n' '-- Boot boundary --'
    ;;
  *) : ;;
esac
""",
    )

    result = subprocess.run(
        ["sh", ROOT / "deploy/selfie-observability/root-helper.sh", "verify"],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert (result.returncode == 0) is expected_success, result.stderr
    if expected_success:
        assert "SELFIE_OBSERVABILITY_HOST_VERIFIED" in result.stdout
    if scenario == "oldest":
        assert "oldest_selfie_event_realtime=100.000000" in result.stdout


@pytest.mark.parametrize(("readable", "expected_success"), [(True, True), (False, False)])
def test_root_helper_verifies_probe_with_privileged_journal_read(
    tmp_path: Path, fake_bin: Path, readable: bool, expected_success: bool
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    env["PROBE_READABLE"] = "1" if readable else "0"
    _write_executable(
        fake_bin / "journalctl",
        """
printf '%s\n' "$*" >> "$COMMAND_LOG.probe-journal"
[ "$PROBE_READABLE" = 1 ] || exit 0
printf '{"probe_id":"00000000-0000-0000-0000-000000000001"}\n'
""",
    )

    result = subprocess.run(
        [
            "sh",
            ROOT / "deploy/selfie-observability/root-helper.sh",
            "verify-probe",
            "00000000-0000-0000-0000-000000000001",
        ],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert (result.returncode == 0) is expected_success, result.stderr
    journal_calls = (tmp_path / "systemctl.log.probe-journal").read_text(encoding="utf-8")
    assert "CONTAINER_TAG=findme.service=web findme.environment=staging" in journal_calls
    assert "CONTAINER_TAG=findme.service=web findme.environment=production" in journal_calls


@pytest.mark.parametrize(
    "arguments",
    [
        ["verify-probe", "not-a-uuid"],
        ["verify-probe", "00000000-0000-0000-0000-000000000001", "extra"],
    ],
)
def test_root_helper_rejects_probe_arguments_before_journal_read(
    tmp_path: Path, fake_bin: Path, arguments: list[str]
) -> None:
    env = _observability_install_env(tmp_path, fake_bin)
    _write_executable(fake_bin / "journalctl", 'printf called > "$COMMAND_LOG.journal-called"')

    result = subprocess.run(
        ["sh", ROOT / "deploy/selfie-observability/root-helper.sh", *arguments],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert not (tmp_path / "systemctl.log.journal-called").exists()


def test_apply_rolls_back_observability_when_post_install_verification_fails(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="observability-verification-failure"),
    )

    assert result.returncode != 0
    commands = _apply_log(tmp_path)
    assert commands.index("observability-install") < commands.index("verify-selfie-observability")
    assert "observability-rollback" in commands
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    sudo_commands = [command for command in commands if command.startswith("sudo ")]
    assert sudo_commands
    assert all("sudo -n " in command and " -E " not in f" {command} " for command in sudo_commands)
    assert all(
        "/usr/local/sbin/findme-selfie-observability" in command for command in sudo_commands
    )
    assert all("/opt/photo-prjct" not in command for command in sudo_commands)
    assert all(
        "new-secret" not in command and "password" not in command for command in sudo_commands
    )


def test_observability_commit_failure_restores_deployed_image_marker(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="observability-commit-failure"),
    )

    assert result.returncode != 0
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert "observability-rollback" in _apply_log(tmp_path)


def test_observability_sudo_preflight_fails_before_deployment_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="sudo-preflight-failure"),
    )

    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    commands = _apply_log(tmp_path)
    assert "sudo -n /usr/local/sbin/findme-selfie-observability install" in commands
    assert "observability-install" in commands
    assert not any(" stop nginx" in command for command in commands)


def test_apply_rolls_back_when_interrupted_at_observability_install_boundary(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="observability-install-signal"),
    )

    assert result.returncode != 0
    commands = _apply_log(tmp_path)
    assert "observability-install" in commands
    assert "observability-rollback" in commands
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not any(" stop nginx" in command for command in commands)


def test_apply_uses_only_the_fixed_root_helper_and_no_general_sudo_probe(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The deployment user must call only the narrow, bootstrapped helper."""
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    assert not any(command == "sudo -n true" for command in commands)
    helper_commands = [
        command for command in commands if "/usr/local/sbin/findme-selfie-observability" in command
    ]
    assert helper_commands[0] == "sudo -n /usr/local/sbin/findme-selfie-observability install"
    assert "sudo -n /usr/local/sbin/findme-selfie-observability verify" in helper_commands
    assert helper_commands[-1] == "sudo -n /usr/local/sbin/findme-selfie-observability commit"
    assert all("/opt/photo-prjct/deploy" not in command for command in helper_commands)


def test_bootstrap_installs_root_owned_helper_and_narrow_sudoers_rule(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The one-time operator step installs immutable helper assets and no broad sudo rule."""
    source = tmp_path / "deploy" / "selfie-observability"
    source.mkdir(parents=True)
    for name in (
        "journald.conf",
        "selfie-search-summary.service",
        "selfie-search-summary.timer",
        "run-daily-summary.sh",
        "summarize.py",
    ):
        shutil.copy2(ROOT / "deploy" / "selfie-observability" / name, source / name)
    helper_source = ROOT / "deploy" / "selfie-observability" / "root-helper.sh"
    if helper_source.exists():
        shutil.copy2(helper_source, source / "root-helper.sh")
    _write_executable(
        fake_bin / "sudo",
        """
printf 'sudo %s\n' "$*" >> "$BOOTSTRAP_LOG"
[ "${1-}" = -n ] && shift
case "${1-}" in
  install)
    shift
    source_path=""
    target_path=""
    is_dir=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -d) is_dir=1 ;;
        -o|-g|-m) shift ;;
        -*) ;;
        *)
          if [ -z "$source_path" ] && [ "$is_dir" -eq 0 ]; then source_path="$1";
          else target_path="$1"; fi
          ;;
      esac
      shift
    done
    case "$target_path" in
      /usr/local/lib/findme-selfie-observability-package*)
        target_path="$BOOTSTRAP_ROOT/package${target_path#/usr/local/lib/findme-selfie-observability-package}"
        ;;
      /usr/local/sbin/findme-selfie-observability.new)
        target_path="$BOOTSTRAP_ROOT/helper.new"
        ;;
      /etc/sudoers.d/findme-selfie-observability.new)
        target_path="$BOOTSTRAP_ROOT/sudoers.d/findme-selfie-observability.new"
        ;;
    esac
    if [ "$is_dir" -eq 1 ]; then
      mkdir -p "$target_path"
    else
      mkdir -p "$(dirname "$target_path")"
      cp "$source_path" "$target_path"
    fi
    ;;
  mv)
    source_path="$2"
    target_path="$3"
    case "$source_path:$target_path" in
      /usr/local/lib/findme-selfie-observability-package/*:*)
        source_path="$BOOTSTRAP_ROOT/package${source_path#/usr/local/lib/findme-selfie-observability-package}"
        ;;
      /usr/local/sbin/findme-selfie-observability.new:*)
        source_path="$BOOTSTRAP_ROOT/helper.new"
        ;;
      /etc/sudoers.d/findme-selfie-observability.new:*)
        source_path="$BOOTSTRAP_ROOT/sudoers.d/findme-selfie-observability.new"
        ;;
    esac
    case "$target_path" in
      /usr/local/lib/findme-selfie-observability-package/*)
        target_path="$BOOTSTRAP_ROOT/package${target_path#/usr/local/lib/findme-selfie-observability-package}"
        ;;
      /usr/local/sbin/findme-selfie-observability)
        target_path="$BOOTSTRAP_ROOT/helper"
        ;;
      /etc/sudoers.d/findme-selfie-observability)
        target_path="$BOOTSTRAP_ROOT/sudoers.d/findme-selfie-observability"
        ;;
    esac
    mkdir -p "$(dirname "$target_path")"
    mv "$source_path" "$target_path"
    ;;
  visudo) ;;
  *) exit 99 ;;
esac
""",
    )
    _write_executable(fake_bin / "systemd-analyze", ":")
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BOOTSTRAP_LOG": str(tmp_path / "bootstrap.log"),
        "DEPLOY_ROOT": str(tmp_path),
        "BOOTSTRAP_ROOT": str(tmp_path),
    }

    result = _run("deploy/bootstrap-selfie-observability.sh", env=env)

    assert result.returncode == 0, result.stderr
    log = (tmp_path / "bootstrap.log").read_text(encoding="utf-8")
    assert "helper" in log
    assert "sudoers" in log
    sudoers = (tmp_path / "sudoers.d/findme-selfie-observability").read_text(encoding="utf-8")
    assert "NOPASSWD: /usr/local/sbin/findme-selfie-observability install" in sudoers
    assert "NOPASSWD: /usr/local/sbin/findme-selfie-observability rollback" in sudoers
    assert "NOPASSWD: /usr/local/sbin/findme-selfie-observability commit" in sudoers
    assert "NOPASSWD: /usr/local/sbin/findme-selfie-observability verify-probe *" in sudoers
    assert "NOPASSWD:ALL" not in sudoers
    assert "/opt/photo-prjct/deploy/install-selfie-observability.sh" not in sudoers


@pytest.mark.parametrize("scenario", ["stale-observability-helper", "stale-observability-package"])
def test_apply_rejects_stale_observability_bootstrap_before_host_mutation(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    result = _run(
        "deploy/apply-deployment.sh", env=_apply_env(tmp_path, fake_bin, scenario=scenario)
    )

    assert result.returncode != 0
    assert "bootstrap is missing or stale" in result.stderr
    commands = _apply_log(tmp_path)
    assert "observability-install" not in commands
    assert not any(" stop nginx" in command for command in commands)


def test_root_helper_reads_only_the_root_owned_package() -> None:
    helper = (ROOT / "deploy/selfie-observability/root-helper.sh").read_text(encoding="utf-8")

    assert "/opt/photo-prjct" not in helper
    assert "/usr/local/lib/findme-selfie-observability-package" in helper
    assert "deploy/selfie-observability" not in helper
    assert "systemd-analyze cat-config systemd/journald.conf" in helper
    assert "systemctl is-enabled --quiet selfie-search-summary.timer" in helper
    assert "systemctl is-active --quiet selfie-search-summary.timer" in helper
    assert "journalctl --disk-usage" in helper


@pytest.mark.parametrize(
    ("scenario", "expected_success"),
    [
        ("ok", True),
        ("no-event", True),
        ("wrong-tag", False),
        ("unreadable-probe", False),
    ],
)
def test_observability_verifier_checks_caps_timer_driver_tags_and_probe(
    tmp_path: Path, fake_bin: Path, scenario: str, expected_success: bool
) -> None:
    (tmp_path / ".env").write_text("APP_IMAGE=test\n", encoding="utf-8")
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.https.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "journal").mkdir()
    _write_executable(
        fake_bin / "systemd-analyze",
        "printf 'Storage=persistent\\nMaxRetentionSec=14day\\nSystemMaxUse=1G\\n'",
    )
    _write_executable(
        fake_bin / "systemctl",
        """
[ "$VERIFY_SCENARIO:$*" != \
  "inactive-timer:is-active --quiet selfie-search-summary.timer" ]
[ "$VERIFY_SCENARIO:$*" != \
  "disabled-timer:is-enabled --quiet selfie-search-summary.timer" ]
""",
    )
    _write_executable(
        fake_bin / "docker",
        """
case "$*" in
  *" ps -q web") printf 'web-id\n' ;;
  *" ps -q nginx") printf 'nginx-id\n' ;;
  *"inspect "*web-id*)
    [ "$VERIFY_SCENARIO" = wrong-tag ] && printf 'json-file|wrong\n' || \
      printf 'journald|findme.service=web findme.environment=staging\n'
    ;;
  *"inspect "*nginx-id*)
    printf 'journald|findme.service=nginx findme.environment=staging\n'
    ;;
  *" exec -T web "*) printf '%s\n' "$*" > "$PROBE_COMMAND_LOG" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "journalctl",
        """
case "$*" in
  *--disk-usage*)
    [ "$VERIFY_SCENARIO" != disk-failure ] || exit 1
    printf 'Archived and active journals take up 12.0M in the file system.\n'
    ;;
  *"-o short-unix"*)
    [ "$VERIFY_SCENARIO" != ordered-oldest ] || printf '100.000000 first\n200.000000 second\n'
    ;;
  *) [ "$VERIFY_SCENARIO" != unreadable-probe ] && printf '%s\n' "$EXPECTED_PROBE_LINE" ;;
esac
""",
    )
    # Deterministic UUID makes the journal harness independent of secret or random output.
    _write_executable(fake_bin / "python3", "printf '00000000-0000-0000-0000-000000000001\n'")
    _write_executable(
        fake_bin / "sudo",
        """
[ "${1-}" = -n ] && shift
[ "${1-}" = /usr/local/sbin/findme-selfie-observability ] || exit 2
[ "${2-}" = verify-probe ] || exit 2
[ "${3-}" = 00000000-0000-0000-0000-000000000001 ] || exit 2
[ "$VERIFY_SCENARIO" != unreadable-probe ]
""",
    )
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOY_ROOT": str(tmp_path),
        "COMPOSE_PROJECT_NAME": "photo-staging",
        "DEPLOYMENT_TARGET": "staging",
        "SELFIE_OBSERVABILITY_JOURNAL_DIR": str(tmp_path / "journal"),
        "VERIFY_SCENARIO": scenario,
        "EXPECTED_PROBE_LINE": '"probe_id":"00000000-0000-0000-0000-000000000001"',
        "PROBE_COMMAND_LOG": str(tmp_path / "probe-command.log"),
    }

    result = _run("deploy/verify-selfie-observability.sh", env=env)

    assert (result.returncode == 0) is expected_success, result.stderr
    probe_command_log = tmp_path / "probe-command.log"
    if scenario != "wrong-tag":
        probe_command = probe_command_log.read_text(encoding="utf-8")
        assert " exec -T web sh -c " in probe_command
        assert "2>/proc/1/fd/2" in probe_command
    if scenario == "disabled-timer":
        assert result.stderr == "selfie summary timer is not enabled\n"
    if scenario == "inactive-timer":
        assert result.stderr == "selfie summary timer is not active\n"


def test_nginx_validation_covers_submission_and_bearer_redaction_contract() -> None:
    validator = (ROOT / "tests/deployment/validate-nginx.sh").read_text(encoding="utf-8")

    for route in (
        "submission_path=",
        "bearer_result_path=",
        "bearer_status_path=",
        "bearer_media_path=",
        "bearer_download_path=",
        "event_path=",
        "static_path=",
        "bearer_4xx_headers=",
    ):
        assert route in validator

    for sentinel in (
        "sentinel-client-ip",
        "sentinel-referrer",
        "sentinel-user-agent",
        "sentinel-tracking",
        "bearer-log-token",
        "sentinel-request-body",
    ):
        assert sentinel in validator

    for assertion in (
        "request_time",
        "status",
        "body_bytes_sent",
        "error_log",
        "ordinary_path",
        "submission_path",
        "ordinary_client_address",
        "client_max_body_size 1k",
        " 413 ",
        "^-",
    ):
        assert assertion in validator
