import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _write_python_executable(path: Path, body: str) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8"
    )
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
    ("overrides", "message"),
    [
        ({"canonical_location": "https://findme-photo.ru/wrong"}, "Location"),
        ({"health_code": "503"}, "HTTPS health"),
    ],
)
def test_public_smoke_rejects_wrong_redirect_or_unhealthy_https(
    tmp_path: Path,
    fake_bin: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    result = _run(
        "deploy/verify-public-edge.sh",
        env=_public_env(tmp_path, fake_bin, **overrides),
    )

    assert result.returncode != 0
    assert message in result.stderr


def _apply_env(
    tmp_path: Path, fake_bin: Path, *, scenario: str, target: str = "production"
) -> dict[str, str]:
    (tmp_path / ".env").write_text(
        "APP_IMAGE=old-image\nSECRET_KEY=old-secret\nKEEP=value\n", encoding="utf-8"
    )
    (tmp_path / "deployed-image").write_text("old-image\n", encoding="utf-8")
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
        deploy_dir / "verify-public-edge.sh",
        """
[ "$(cat "$DEPLOY_ROOT/deployed-image")" = old-image ]
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
            def order_by(self, field):
                if field != "id":
                    raise RuntimeError("candidate query must use stable id ordering")
                record("preflight-order-by id")
                return self

            def first(self):
                record("preflight-first")
                if scenario in {"private-media-success", "private-media-failure"}:
                    return types.SimpleNamespace(original_key="originals/eligible-photo")
                return None


        class Manager:
            def filter(self, **filters):
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
case " $* " in
  *" run --rm --no-deps -T --entrypoint python web manage.py shell -c "*)
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
    GALLERY_PREFLIGHT="$gallery_preflight" "$GALLERY_PREFLIGHT_HARNESS"
    exit $?
    ;;
esac
printf 'APP_IMAGE=%s docker %s\n' "${APP_IMAGE-unset}" "$*" >> "$COMMAND_LOG"
case " $* " in
  *" compose "*" pull "*) [ "$APPLY_SCENARIO" != pull-failure ] ;;
  *" compose "*" ps -q web "*) printf 'web-id\n' ;;
  *" inspect "*" web-id "*) sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "curl",
        """
printf 'curl %s\n' "$*" >> "$COMMAND_LOG"
if [ "$APPLY_SCENARIO" = health-failure ]; then
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
case "$*" in
  *"/deployed-image") [ "$APPLY_SCENARIO" != marker-failure ] || exit 1 ;;
esac
/bin/mv "$@"
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(tmp_path / "apply.log"),
        "GALLERY_PREFLIGHT_HARNESS": str(gallery_preflight_harness),
        "APPLY_SCENARIO": scenario,
        "DEPLOYMENT_TARGET": target,
        "DEPLOY_ROOT": str(tmp_path),
        "COMPOSE_PROJECT_NAME": f"photo-{target}",
        "APP_IMAGE": "new-image",
        "SECRET_KEY": "new-secret",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "localhost",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "password",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": "",
        "LETSENCRYPT_EMAIL": "ops@example.com",
    }


def _apply_log(tmp_path: Path) -> list[str]:
    return (tmp_path / "apply.log").read_text(encoding="utf-8").splitlines()


def test_apply_propagates_private_media_read_settings(
    tmp_path: Path, fake_bin: Path
) -> None:
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


def test_candidate_private_media_preflight_skips_when_no_eligible_photo(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    assert [
        line for line in result.stdout.splitlines() if line.startswith("gallery-")
    ] == ["gallery-private-media-preflight-skipped:no-eligible-photo"]
    commands = _apply_log(tmp_path)
    assert "preflight-filter eligible-private-photo" in commands
    assert "preflight-order-by id" in commands
    assert "preflight-first" in commands
    assert not any(command.startswith("preflight-storage") for command in commands)
    assert not any(command.startswith("preflight-open") for command in commands)
    assert not any(command.startswith("preflight-read") for command in commands)


def test_candidate_private_media_preflight_reads_when_photo_exists(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-success"),
    )

    assert result.returncode == 0, result.stderr
    assert [
        line for line in result.stdout.splitlines() if line.startswith("gallery-")
    ] == ["gallery-private-media-preflight-ok"]
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
    candidate_run = commands.index(
        "APP_IMAGE=new-image docker compose --project-name photo-production "
        f"--env-file {tmp_path}/.env -f {tmp_path}/docker-compose.prod.yml "
        f"-f {tmp_path}/docker-compose.https.yml run --rm --no-deps -T "
        "--entrypoint python web manage.py shell -c <gallery_media_preflight>"
    )
    stop_nginx = next(
        index for index, command in enumerate(commands) if " stop nginx" in command
    )
    candidate_up = next(
        index
        for index, command in enumerate(commands)
        if " up -d --remove-orphans" in command and "APP_IMAGE=new-image" in command
    )
    assert candidate_pull < candidate_run < stop_nginx < candidate_up


def test_failed_candidate_private_media_preflight_with_photo_keeps_old_service_active(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-failure"),
    )

    assert result.returncode != 0
    assert "Gallery private-media read prerequisite failed" in result.stderr
    assert "private failure detail" not in result.stderr
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith(
        "APP_IMAGE=old-image\n"
    )
    commands = _apply_log(tmp_path)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(
        "APP_IMAGE=new-image" in command and " up -d --remove-orphans" in command
        for command in commands
    )


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


def test_deployment_path_performs_no_iam_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
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
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.count("up -d --remove-orphans") == 1
    assert "https://findme-photo.ru/health/" in commands


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
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("APP_IMAGE=old-image\n")
    assert "SECRET_KEY=new-secret\n" in (tmp_path / ".env").read_text(encoding="utf-8")
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
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("APP_IMAGE=old-image\n")
    assert "SECRET_KEY=new-secret\n" in (tmp_path / ".env").read_text(encoding="utf-8")
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.index("stop nginx") < commands.index("up -d --remove-orphans")
    assert "docker-compose.https.yml" in commands


@pytest.mark.parametrize(
    ("scenario", "expected_reconciliations"),
    [("pull-failure", 1), ("marker-failure", 2)],
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
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("APP_IMAGE=old-image\n")
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
