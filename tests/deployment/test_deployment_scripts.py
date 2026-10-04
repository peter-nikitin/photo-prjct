import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "persisted,installed,inspection_ok,healthy,expected",
    [
        ("web", "web-next", True, "web", 2),
        ("web-next", "web", True, "web-next", 2),
        (None, "web-next", False, "web", 2),
        ("web-next", "web-next", True, "web-next", 0),
        (None, None, True, "web", 0),
        ("web", "web", True, None, 2),
    ],
    ids=[
        "mismatch-web",
        "mismatch-next",
        "inspection-failed",
        "persisted-restart",
        "fresh-start",
        "unhealthy-persisted",
    ],
)
def test_nginx_startup_preflight_preserves_selection(
    tmp_path, fake_bin, persisted, installed, inspection_ok, healthy, expected
):
    marker = tmp_path / "selected-slot"
    if persisted is not None:
        marker.write_text(persisted + "\n")
    configuration = tmp_path / "installed.conf"
    configuration.write_text(
        f"upstream django_upstream {{\n    server {installed}:8000;\n}}\n"
        if installed is not None
        else "server { listen 80; }\n"
    )
    _write_executable(
        fake_bin / "nginx",
        """
[ "$*" = "-T" ] || exit 3
[ "$INSPECTION_OK" = 1 ] || exit 1
cat "$INSTALLED_CONFIGURATION"
""",
    )
    _write_executable(
        fake_bin / "wget",
        """
for argument do :; done
[ "$argument" = "http://$HEALTHY_SLOT:8000/health/" ]
""",
    )
    # Run the actual startup selection prefix; substitute only the host-owned
    # marker path and external processes. Rendering/startup are covered in real
    # Nginx validation separately, so this focused check cannot start a daemon.
    source = (ROOT / "deploy/nginx/reload-nginx.sh").read_text()
    preflight = source.split('if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then', 1)[0]
    preflight = preflight.replace("/opt/nginx/selected-slot", '"${STARTUP_MARKER}"')
    result = subprocess.run(
        ["/bin/sh"],
        input=preflight + '\nprintf "ACCEPTED %s\\n" "$DJANGO_SLOT"\n',
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PUBLIC_DOMAIN": "findme-photo.ru",
            "STARTUP_MARKER": str(marker),
            "INSTALLED_CONFIGURATION": str(configuration),
            "INSPECTION_OK": str(int(inspection_ok)),
            "HEALTHY_SLOT": healthy or "none",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 0:
        assert result.stdout == f"ACCEPTED {persisted or 'web'}\n"
    else:
        assert "ACCEPTED" not in result.stdout
    assert marker.exists() == (persisted is not None)
    if persisted is not None:
        assert marker.read_text() == persisted + "\n"


def _slot_command(root: Path, *arguments: str, env=None):
    return subprocess.run(
        [sys.executable, ROOT / "deploy/web-slot.py", "--root", root, *arguments],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("slot", ["web", "web-next"])
def test_web_slot_switch_is_durable_and_uses_validated_reload(tmp_path, fake_bin, slot):
    configuration = tmp_path / "deploy/nginx"
    configuration.mkdir(parents=True)
    (configuration / "selected-slot").write_text("web\n")
    log = tmp_path / "commands"
    installed = tmp_path / "installed-slot"
    installed.write_text("web")
    _write_executable(
        fake_bin / "docker",
        """
printf '%s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *"nginx -T")
    printf 'upstream django_upstream { server %s:8000; }\\n' "$(cat "$INSTALLED_SLOT")" ;;
  *"--apply --slot "*) for slot do :; done; printf '%s' "$slot" > "$INSTALLED_SLOT" ;;
  *"ps -q "*) printf 'target-id\\n' ;;
  "inspect "*) printf 'healthy\\n' ;;
  *) exit 2 ;;
esac
""",
    )
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "INSTALLED_SLOT": str(installed),
    }
    result = _slot_command(tmp_path, "switch", slot, env=env)
    assert result.returncode == 0, result.stderr
    assert _slot_command(tmp_path, "selected", env=env).stdout == f"{slot}\n"
    assert (
        f"exec -T nginx /bin/sh /opt/nginx/reload-nginx.sh --apply --slot {slot}" in log.read_text()
    )
    assert "--project-name photo-prjct" in log.read_text()
    assert "stop" not in log.read_text() and "up -d" not in log.read_text()


def test_web_slot_failed_validation_keeps_selected_predecessor(tmp_path, fake_bin):
    configuration = tmp_path / "deploy/nginx"
    configuration.mkdir(parents=True)
    (configuration / "selected-slot").write_text("web\n")
    _write_executable(
        fake_bin / "docker",
        """
case "$*" in
  *"nginx -T") printf 'upstream django_upstream { server web:8000; }\\n' ;;
  *"ps -q "*) printf 'target-id\\n' ;;
  "inspect "*) printf 'healthy\\n' ;;
  *) exit 1 ;;
esac
""",
    )
    env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    result = _slot_command(tmp_path, "switch", "web-next", env=env)
    assert result.returncode != 0
    assert _slot_command(tmp_path, "selected", env=env).stdout == "web\n"


def test_web_slot_mismatch_requires_explicit_reconciliation(tmp_path, fake_bin):
    configuration = tmp_path / "deploy/nginx"
    configuration.mkdir(parents=True)
    (configuration / "selected-slot").write_text("web\n")
    _write_executable(
        fake_bin / "docker",
        """
case "$*" in
  *"nginx -T") printf 'upstream django_upstream { server web-next:8000; }\\n' ;;
  *"--apply --slot web") exit 0 ;;
  *"ps -q "*) printf 'target-id\\n' ;;
  "inspect "*) printf 'healthy\\n' ;;
  *) exit 2 ;;
esac
""",
    )
    env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    result = _slot_command(tmp_path, "selected", env=env)
    assert result.returncode == 1 and result.stdout == ""
    assert "installed=web-next persisted=web" in result.stderr
    assert _slot_command(tmp_path, "switch", "web", env=env).returncode == 0


@pytest.mark.parametrize("health", ["healthy", "unhealthy"])
def test_explicit_switch_recovers_first_interruption_only_to_a_healthy_slot(
    tmp_path, fake_bin, health
):
    configuration = tmp_path / "deploy/nginx"
    configuration.mkdir(parents=True)
    log = tmp_path / "commands"
    _write_executable(
        fake_bin / "docker",
        """
printf '%s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *"nginx -T") printf 'upstream django_upstream { server web-next:8000; }\\n' ;;
  *"ps -q web") printf 'old-id\\n' ;;
  "inspect "*) printf '%s\\n' "$HEALTH" ;;
  *"--apply --slot web") exit 0 ;;
  *) exit 2 ;;
esac
""",
    )
    result = _slot_command(
        tmp_path,
        "switch",
        "web",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
            "HEALTH": health,
        },
    )
    if health == "healthy":
        assert result.returncode == 0, result.stderr
        assert (configuration / "selected-slot").read_text() == "web\n"
    else:
        assert result.returncode == 1
        assert "not healthy" in result.stderr
        assert "--apply" not in log.read_text()
        assert not (configuration / "selected-slot").exists()


def test_web_slot_rejects_invalid_selection_before_edge_mutation(tmp_path, fake_bin):
    result = _slot_command(tmp_path, "switch", "web; injected", env={"PATH": str(fake_bin)})
    assert result.returncode != 0
    assert not (tmp_path / "deploy/nginx/selected-slot").exists()


def test_web_slot_static_seed_preserves_predecessor_assets(tmp_path, fake_bin):
    source = tmp_path / "source"
    source.mkdir()
    (source / "site.abcdef123456.css").write_text("old")
    (source / "staticfiles.json").write_text("old manifest")
    destination = tmp_path / "static"
    destination.mkdir()
    (destination / "site.123456abcdef.css").write_text("new")
    (destination / "staticfiles.json").write_text("candidate manifest")
    _write_python_executable(
        fake_bin / "docker",
        """
import os, subprocess, sys
arguments = sys.argv[1:]
if arguments[-3:] == ['ps', '-q', 'web']:
    print('active-web-id')
elif arguments == ['cp', 'active-web-id:/app/src/backend/staticfiles/.', '-']:
    subprocess.run(['tar', '-cf', '-', '-C', os.environ['SOURCE_STATIC'], '.'], check=True)
elif '--entrypoint' in arguments and arguments[arguments.index('--entrypoint') + 1] == 'python':
    script = arguments[-1].replace('/app/src/backend/staticfiles', os.environ['DESTINATION_STATIC'])
    subprocess.run([sys.executable, '-c', script], check=True)
else:
    sys.exit(2)
""",
    )
    result = _slot_command(
        tmp_path,
        "seed-static",
        "web",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "SOURCE_STATIC": str(source),
            "DESTINATION_STATIC": str(destination),
        },
    )
    assert result.returncode == 0, result.stderr
    assert (destination / "site.abcdef123456.css").read_text() == "old"
    assert (destination / "site.123456abcdef.css").read_text() == "new"
    assert (destination / "staticfiles.json").read_text() == "candidate manifest"


@pytest.mark.parametrize(
    "configuration,health,expected",
    [
        ("upstream django_upstream { server web:8000; }", "healthy", 0),
        ("upstream django_upstream { server web-next:8000; }", "healthy", 1),
        ("upstream django_upstream { server web:8000; }", "unhealthy", 1),
    ],
)
def test_missing_slot_selection_requires_live_single_slot_proof(
    tmp_path, fake_bin, configuration, health, expected
):
    _write_executable(
        fake_bin / "docker",
        """
case "$*" in
  *"nginx -T") printf '%s\\n' "$CONFIGURATION" ;;
  *"ps -q web") printf 'active-id\\n' ;;
  "inspect "*) printf '%s\\n' "$HEALTH" ;;
  *) exit 2 ;;
esac
""",
    )
    result = _slot_command(
        tmp_path,
        "selected",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CONFIGURATION": configuration,
            "HEALTH": health,
        },
    )
    assert result.returncode == expected, result.stderr
    assert result.stdout == ("web\n" if expected == 0 else "")


@pytest.mark.parametrize("previous_placement", ["local", "remote"])
@pytest.mark.parametrize("requested_commerce", ["True", "False"])
@pytest.mark.parametrize("previous_commerce", ["True", "False"])
def test_remote_application_reconciles_commerce_forward_and_after_failed_candidate(
    tmp_path, previous_placement, requested_commerce, previous_commerce
):
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    functions = "\n".join(
        re.search(rf"^{name}\(\) \{{\n.*?^\}}", source, re.M | re.S)[0]
        for name in (
            "compose_reconcile_requested_runtime_profiles",
            "clear_candidate_compose_interpolation",
            "recover_previous_deployment",
            "restore_previous_deployment_package",
            "retain_vector_database_image",
        )
    )
    previous_package = tmp_path / "previous-package"
    (previous_package / "deploy").mkdir(parents=True)
    (tmp_path / "deploy").mkdir()
    for root in (tmp_path, previous_package):
        (root / "docker-compose.deployment.yml").write_text(
            "services:\n  db:\n    image: postgres:16\n  web:\n    image: previous-package\n"
        )
        (root / "docker-compose.https.yml").write_text("services: {}\n")
    database_image = (
        "pgvector/pgvector:0.8.6-pg16-trixie@sha256:"
        "c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b"
    )
    (tmp_path / ".env").write_text("APP_IMAGE=candidate-image\n")
    previous_env = tmp_path / "previous.env"
    previous_env.write_text("APP_IMAGE=previous-image\n")
    result = subprocess.run(
        [
            "/bin/sh",
            "-eu",
            "-c",
            functions
            + """
compose() { printf '%s %s\n' "$(sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env")" "$*"; }
fleet_phase() { [ "$1" = rollback ]; }
stop_import_before_web_change() { :; }
previous_web_matches_processing_schema() { :; }
restore_previous_deployment_markers() { :; }
compose_reconcile_requested_runtime_profiles
recover_previous_deployment
""",
        ],
        env={
            **os.environ,
            "DEPLOY_ROOT": str(tmp_path),
            "requested_worker_placement": "remote",
            "worker_pool_activation": "normal",
            "previous_worker_placement": previous_placement,
            "requested_commerce_worker_enabled": requested_commerce,
            "previous_commerce_worker_enabled": previous_commerce,
            "previous_processing_enabled": "True",
            "previous_worker_replicas": "1",
            "requested_import_enabled": "False",
            "previous_import_enabled": "False",
            "candidate_import_worker_start_attempted": "0",
            "fleet_prepared": "1",
            "previous_env_exists": "1",
            "previous_env_tmp": str(previous_env),
            "PREVIOUS_DEPLOYMENT_PACKAGE_ROOT": str(previous_package),
            "vector_database_reconciled": "1",
            "vector_database_image": database_image,
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    commerce = [line for line in result.stdout.splitlines() if "commerce-worker" in line]
    commands = {
        "True": "--profile commerce up -d --no-deps commerce-worker",
        "False": "--profile commerce rm -sf commerce-worker",
    }
    assert commerce == [
        f"candidate-image {commands[requested_commerce]}",
        f"previous-image {commands[previous_commerce]}",
    ]
    assert (tmp_path / ".env").read_text() == "APP_IMAGE=previous-image\n"
    assert "rm -sf worker" not in result.stdout
    assert "--remove-orphans" not in result.stdout
    assert "postgres" not in result.stdout
    assert f"image: {database_image}" in (tmp_path / "docker-compose.deployment.yml").read_text()
    assert not previous_package.exists()


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


def _real_commerce_worker_settings() -> dict[str, str]:
    return {
        "COMMERCE_WORKER_ENABLED": "True",
        "COMMERCE_PUBLIC_ORIGIN": "https://findme-photo.ru",
        "COMMERCE_PAYMENT_GATEWAY_FACTORY": (
            "commerce.payment_simulator.payment_simulator_gateway_factory"
        ),
        "COMMERCE_EMAIL_SENDER_FACTORY": (
            "commerce.postbox_email_sender.postbox_email_sender_factory"
        ),
        "COMMERCE_WORKER_FACTORY": "commerce.runtime.commerce_worker_factory",
        "COMMERCE_EMAIL_FROM_ADDRESS": "orders@findme-photo.ru",
        "COMMERCE_POSTBOX_API_KEY_ID": "postbox-api-key-id",
        "COMMERCE_POSTBOX_API_KEY_SECRET": "postbox-secret-must-not-be-logged",
        "COMMERCE_ORDER_ACCESS_SIGNING_SECRET": "commerce-signing-secret",
        "COMMERCE_SUPPORT_CONTACT": "support@findme-photo.ru",
    }


def _cart_cleanup_block(deploy_root: Path) -> str:
    return (
        "# BEGIN photo-prjct-cart-cleanup\n"
        f"23 3 * * * DEPLOY_ROOT={deploy_root} /bin/sh "
        f"{deploy_root}/deploy/run-cart-cleanup.sh >> "
        f"{deploy_root}/cart-cleanup.log 2>&1\n"
        "# END photo-prjct-cart-cleanup\n"
    )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _write_python_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _run(script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", ROOT / script],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def test_commerce_health_probe_uses_canonical_compose_identity_and_safe_command(
    tmp_path: Path, fake_bin: Path
) -> None:
    commands = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "docker",
        """
printf '%s\\n' "$*" >> "$COMMAND_LOG"
case " $* " in
  *" --project-name photo-prjct "*" commerce_worker_health "*) exit 0 ;;
  *) exit 1 ;;
esac
""",
    )
    deployment_root = tmp_path / "deployment"
    deployment_root.mkdir()
    (deployment_root / ".env").write_text("COMMERCE_ORDER_ACCESS_SIGNING_SECRET=secret\\n")

    result = _run(
        "deploy/run-commerce-worker-health.sh",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(commands),
            "DEPLOY_ROOT": str(deployment_root),
        },
    )

    assert result.returncode == 0, result.stderr
    command = commands.read_text(encoding="utf-8")
    assert "--project-name photo-prjct" in command
    assert " exec -T web " in f" {command} "
    assert "commerce_worker_health --max-ready-age-seconds" in command
    assert "COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS" in command
    assert "commerce-worker" not in command
    assert "COMMERCE_ORDER_ACCESS_SIGNING_SECRET" not in result.stdout + result.stderr


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


def test_cart_cleanup_cron_is_idempotent_bounded_and_removable(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The host schedule is one managed daily command, never a feature activation."""
    crontab_state = tmp_path / "crontab"
    command_log = tmp_path / "cart-cleanup.log"
    _write_executable(
        fake_bin / "crontab",
        """
if [ "${1-}" = -l ]; then
  [ -f "$CRONTAB_STATE" ] || exit 1
  cat "$CRONTAB_STATE"
  exit 0
fi
cat "$1" > "$CRONTAB_STATE"
""",
    )
    _write_executable(
        fake_bin / "flock",
        """
printf 'flock %s\\n' "$*" >> "$COMMAND_LOG"
shift 4
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "docker",
        """
printf 'docker %s\\n' "$*" >> "$COMMAND_LOG"
""",
    )
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CRONTAB_STATE": str(crontab_state),
        "COMMAND_LOG": str(command_log),
        "DEPLOY_ROOT": str(ROOT),
    }

    installed = _run("deploy/install-cart-cleanup-cron.sh", env=env)
    repeated = _run("deploy/install-cart-cleanup-cron.sh", env=env)
    cleanup = _run("deploy/run-cart-cleanup.sh", env=env)
    assert installed.returncode == repeated.returncode == cleanup.returncode == 0
    assert "Installed daily cart cleanup" in installed.stdout
    schedule = crontab_state.read_text(encoding="utf-8")
    assert schedule.count("# BEGIN photo-prjct-cart-cleanup") == 1
    assert schedule.count("# END photo-prjct-cart-cleanup") == 1
    assert (
        f"23 3 * * * DEPLOY_ROOT={ROOT} /bin/sh "
        f"{ROOT}/deploy/run-cart-cleanup.sh >> "
        f"{ROOT}/cart-cleanup.log 2>&1"
    ) in schedule
    commands = command_log.read_text(encoding="utf-8")
    assert f"flock -n -E 75 {ROOT}/cart-cleanup.lock" in commands
    assert "compose --project-name photo-prjct" in commands
    assert "exec -T web python manage.py cleanup_expired_carts --limit 1000" in commands

    explicit_remove = subprocess.run(
        ["sh", ROOT / "deploy/install-cart-cleanup-cron.sh", "remove"],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )
    assert explicit_remove.returncode == 0
    assert "# BEGIN photo-prjct-cart-cleanup" not in crontab_state.read_text(encoding="utf-8")

    unsafe = _run(
        "deploy/install-cart-cleanup-cron.sh",
        env={**env, "DEPLOY_ROOT": "/srv/photo; rm"},
    )
    assert unsafe.returncode == 2
    assert "unsupported characters" in unsafe.stderr


def _apply_env(
    tmp_path: Path,
    fake_bin: Path,
    *,
    scenario: str,
) -> dict[str, str]:
    (tmp_path / ".env").write_bytes(PREVIOUS_ENV)
    (tmp_path / ".env").chmod(0o640)
    (tmp_path / "previous-env.expected").write_bytes(PREVIOUS_ENV)
    (tmp_path / "deployed-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / "worker-pools-current.json").write_text("{}\n")
    (tmp_path / "worker-pools-release.json").write_text('{"phase":"committed"}\n')
    for name in ("docker-compose.deployment.yml", "docker-compose.https.yml"):
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
    shutil.copy2(
        ROOT / "deploy/install-cart-cleanup-cron.sh",
        deploy_dir / "install-cart-cleanup-cron.sh",
    )
    shutil.copy2(ROOT / "deploy/run-cart-cleanup.sh", deploy_dir / "run-cart-cleanup.sh")
    shutil.copy2(
        ROOT / "deploy/run-commerce-worker-health.sh",
        deploy_dir / "run-commerce-worker-health.sh",
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
if [ "${1-}" = /usr/local/sbin/findme-worker-pool-metrics ]; then
  printf 'worker-metrics-%s\n' "${2-}" >> "$COMMAND_LOG"
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
  [ "$(cat "$DEPLOY_ROOT/deployed-image")" = "${EXPECTED_DEPLOYED_IMAGE:-old-image}" ]
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
if [ "${1-}" = image ] && [ "${2-}" = prune ]; then
  printf 'docker %s\n' "$*" >> "$COMMAND_LOG"
  [ "$APPLY_SCENARIO" != image-prune-failure ]
  exit
fi
case " $* " in
  *"ingestion_importattempt"*)
    printf 'import-lease-probe\n' >> "$COMMAND_LOG"
    if [ -n "${IMPORT_LEASE_PROBE_FILE:-}" ]; then
      probe_result=$(sed -n '1p' "$IMPORT_LEASE_PROBE_FILE")
      sed '1d' "$IMPORT_LEASE_PROBE_FILE" > "$IMPORT_LEASE_PROBE_FILE.next"
      mv "$IMPORT_LEASE_PROBE_FILE.next" "$IMPORT_LEASE_PROBE_FILE"
    else
      probe_result=0
    fi
    [ "$probe_result" != error ] || exit 1
    printf '%s\n' "$probe_result"
    exit 0
    ;;
esac
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
  [ "$(sed -n 's/^APP_IMAGE=//p' "$compose_env_file")" = "${EXPECTED_REQUESTED_IMAGE:-new-image}" ]
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
  *"org.opencontainers.image.revision"*)
    printf '%s\n' "${EXPECTED_IMAGE_REVISION:-unknown}"
    exit 0
    ;;
  *"FORWARD_RECOVERY_PROTOCOL"*)
    validate_candidate_env
    printf 'forward-candidate-probe\n' >> "$COMMAND_LOG"
    case "$APPLY_SCENARIO" in
      forward-incompatible|forward-unpaused)
        printf 'private-forward-probe-detail\n' >&2
        exit 1
        ;;
    esac
    exit 0
    ;;
  *"pg_database_collation_actual_version"*)
    validate_candidate_env
    printf 'candidate-vector-collation-check\n' >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != vector-collation-mismatch ]
    ;;
  *"CREATE EXTENSION IF NOT EXISTS vector"*)
    validate_candidate_env
    printf 'candidate-vector-capability\n' >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != vector-capability-failure ]
    ;;
  *" up -d --wait --no-deps db "*)
    validate_candidate_env
    printf 'candidate-vector-database-start\n' >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != vector-database-start-failure ]
    ;;
  *" run --rm --no-deps -T --entrypoint python web manage.py migrate --noinput "*)
    validate_candidate_env
    printf 'candidate-migrate\n' >> "$COMMAND_LOG"
    case "$APPLY_SCENARIO" in
      processing-schema-migrate-failure|processing-schema-drain-failure|processing-schema-health-failure)
        touch "$DEPLOY_ROOT/processing-column-dropped"
        ;;
    esac
    case "$APPLY_SCENARIO" in
      gallery-projection-migration-failure|processing-schema-migrate-failure) exit 1 ;;
    esac
    ;;
  *"list(ProcessingAttempt.objects.all()[:1])"*)
    [ "${APP_IMAGE-unset}" = unset ]
    cmp "$compose_env_file" "$PREVIOUS_ENV_EXPECTED"
    case " $* " in *"SET TRANSACTION READ ONLY"*"statement_timeout"*) : ;; *) exit 1 ;; esac
    printf 'previous-web-processing-schema-probe\n' >> "$COMMAND_LOG"
    if [ -f "$DEPLOY_ROOT/processing-column-dropped" ]; then
      printf 'private-db-detail-must-not-reach-output\n' >&2
      exit 1
    fi
    ;;
  *" drain_gallery_media_publications --all-events "*)
    validate_candidate_env
    printf 'candidate-gallery-publication-drain\n' >> "$COMMAND_LOG"
    if [ "$APPLY_SCENARIO" = gallery-projection-publication-drain-failure ] ||
       [ "$APPLY_SCENARIO" = processing-schema-drain-failure ]; then
      printf 'private-key-must-not-reach-output photo-id-must-not-reach-output\n' >&2
      exit 1
    fi
    ;;
  *" rebuild_gallery_media_projection --all-events --apply "*)
    validate_candidate_env
    printf 'candidate-gallery-projection-rebuild\n' >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != gallery-projection-rebuild-failure ]
    ;;
  *" verify_gallery_media_projection --all-events --require-clean "*)
    validate_candidate_env
    printf 'candidate-gallery-projection-verify\n' >> "$COMMAND_LOG"
    if [ "$APPLY_SCENARIO" = gallery-projection-verification-failure ]; then
      printf 'private-key-must-not-reach-output photo-id-must-not-reach-output\n' >&2
      exit 1
    fi
    ;;
  *" exec -T web python manage.py smoke_gallery_media_projection "*)
    printf 'candidate-gallery-projection-smoke\n' >> "$COMMAND_LOG"
    if [ "$APPLY_SCENARIO" = gallery-projection-smoke-failure ]; then
      printf 'private-key-must-not-reach-output photo-id-must-not-reach-output\n' >&2
      exit 1
    fi
    ;;
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
    for candidate_shell_program do :; done
    gallery_preflight="$candidate_shell_program"
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
    [ "$(sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env")" = \
      "${EXPECTED_REQUESTED_IMAGE:-new-image}" ]
    [ "$(sed -n 's/^SECRET_KEY=//p' "$DEPLOY_ROOT/.env")" = new-secret ]
    printf 'requested-env-promoted-before-stop\n' >> "$COMMAND_LOG"
    ;;
esac
if [ -n "${RECOVERY_COMPOSE_CONFIG:-}" ] && \
   [ "${APP_IMAGE-unset}" = unset ] && \
   case " $* " in *" compose "*" up -d --no-deps web nginx "*) true ;; *) false ;; esac; then
  "$REAL_DOCKER" compose --env-file "$compose_env_file" \
    -f "$REAL_DEPLOYMENT_COMPOSE" config --environment \
    > "$RECOVERY_COMPOSE_CONFIG" 2> "$RECOVERY_COMPOSE_STDERR"
fi
printf 'APP_IMAGE=%s docker %s\n' "${APP_IMAGE-unset}" "$*" >> "$COMMAND_LOG"
if [ "${1-}" = ps ] && [ "${2-}" = -q ]; then
  case " $* " in
    *"com.docker.compose.service=worker-bulk"*) printf 'previous-worker-bulk\n' ;;
    *"com.docker.compose.service=worker-selfie"*) printf 'previous-worker-selfie\n' ;;
    *"com.docker.compose.service=worker"*) : ;;
  esac
  exit 0
fi
if [ "${1-}" = stop ] && [ "$APPLY_SCENARIO" = gallery-projection-worker-stop-failure ]; then
  exit 1
fi
if [ "$APPLY_SCENARIO" = worker-removal-failure ] && \
   case " $* " in
     *" compose "*" --profile worker rm -sf worker-bulk worker-selfie "*) true ;;
     *) false ;;
   esac; then
  exit 1
fi
if [ "$APPLY_SCENARIO" = worker-recovery ] && \
   [ "${APP_IMAGE-unset}" = unset ] && \
   case " $* " in *" compose "*" up -d --no-deps web nginx "*) true ;; *) false ;; esac; then
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
   case " $* " in
     *" compose "*" --profile worker rm -sf worker-bulk worker-selfie "*) true ;;
     *) false ;;
   esac; then
  [ "$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$compose_env_file")" = False ]
  printf 'recovery-removes-worker-from-restored-disabled-environment\n' >> "$COMMAND_LOG"
fi
if [ "$APPLY_SCENARIO" = compose-failure ] && \
   [ "${APP_IMAGE-unset}" = new-image ] && \
   case " $* " in *" compose "*" up -d --no-deps web nginx "*) true ;; *) false ;; esac; then
  exit 1
fi
if [ "$APPLY_SCENARIO" = recovery-failure ] && \
   case " $* " in *" compose "*" up -d --no-deps web nginx "*) true ;; *) false ;; esac; then
  exit 1
fi
case " $* " in
  *" compose "*" pull "*) [ "$APPLY_SCENARIO" != pull-failure ] ;;
  *" compose "*" ps -q web "*) printf 'web-id\n' ;;
  *" compose "*" ps -q worker-bulk "*)
    [ "$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$DEPLOY_ROOT/.env")" = True ] || exit 0
    worker_replicas="$(sed -n 's/^PHOTO_WORKER_REPLICAS=//p' "$DEPLOY_ROOT/.env" | head -n 1)"
    case "$APPLY_SCENARIO" in
      worker-second-missing)
        printf 'worker-bulk-first\n'
        ;;
      *)
        printf 'worker-bulk-first\n'
        if [ "$worker_replicas" = 2 ]; then
          printf 'worker-bulk-second\n'
        fi
        ;;
    esac
    ;;
  *" compose "*" ps -q worker-selfie "*)
    [ "$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$DEPLOY_ROOT/.env")" = True ] || exit 0
    printf 'worker-selfie\n'
    ;;
  *" compose "*" ps -q commerce-worker "*)
    [ "$(sed -n 's/^COMMERCE_WORKER_ENABLED=//p' "$DEPLOY_ROOT/.env")" = True ] || exit 0
    printf 'commerce-worker-id\n'
    ;;
  *" inspect "*" web-id "*) sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env" ;;
  *" inspect "*" commerce-worker-id "*) printf 'true false false\n' ;;
  *" inspect "*" worker-bulk-first "*|*" inspect "*" worker-selfie "*)
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
  *" inspect "*" worker-bulk-second "*)
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
  *" compose "*" exec -T web sh -c "*" commerce_worker_health "*)
    commerce_health_attempt=0
    if [ -f "$COMMERCE_HEALTH_ATTEMPTS" ]; then
      commerce_health_attempt="$(cat "$COMMERCE_HEALTH_ATTEMPTS")"
    fi
    commerce_health_attempt=$((commerce_health_attempt + 1))
    printf '%s\n' "$commerce_health_attempt" > "$COMMERCE_HEALTH_ATTEMPTS"
    printf 'commerce-worker-health-attempt=%s\n' "$commerce_health_attempt" >> "$COMMAND_LOG"
    case "$APPLY_SCENARIO" in
      commerce-worker-ready-after-retry)
        [ "$commerce_health_attempt" -ge 3 ]
        ;;
      commerce-worker-health-failure)
        exit 1
        ;;
    esac
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "curl",
        """
printf 'curl %s\n' "$*" >> "$COMMAND_LOG"
if [ "$APPLY_SCENARIO" = health-failure ] || \
   [ "$APPLY_SCENARIO" = processing-schema-health-failure ] || \
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
  [ -f "$CRONTAB_STATE" ] || exit 1
  cat "$CRONTAB_STATE"
  exit 0
fi
printf 'crontab %s\n' "$*" >> "$COMMAND_LOG"
cat "$1" > "$CRONTAB_STATE"
""",
    )
    _write_executable(fake_bin / "flock", ":")
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
        "COMMERCE_HEALTH_ATTEMPTS": str(tmp_path / "commerce-health-attempts"),
        "CRONTAB_STATE": str(tmp_path / "crontab"),
        "PREVIOUS_ENV_EXPECTED": str(tmp_path / "previous-env.expected"),
        "GALLERY_PREFLIGHT_HARNESS": str(gallery_preflight_harness),
        "APPLY_SCENARIO": scenario,
        "DEPLOY_ROOT": str(tmp_path),
        "COMPOSE_PROJECT_NAME": "photo-prjct",
        "APP_IMAGE": "new-image",
        "SECRET_KEY": "new-secret",
        "EXPECTED_REQUESTED_SECRET": "new-secret",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "localhost",
        "GUNICORN_WORKERS": "5",
        "GUNICORN_THREADS": "2",
        "GUNICORN_TIMEOUT": "180",
        "GUNICORN_MAX_REQUESTS": "0",
        "GUNICORN_MAX_REQUESTS_JITTER": "0",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "password",
        "PHOTO_UPLOAD_ENABLED": "False",
        "PHOTO_PROCESSING_ENABLED": "True",
        "PHOTO_PROCESSING_FACE_ENABLED": "True",
        "WORKER_IMAGE": "worker-image",
        "PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token",
        "WORKER_POOL_PRIVATE_API_IPV4": "10.0.0.2",
        "WORKER_POOL_RELEASE_MANIFEST": "reviewed-manifest",
        "WORKER_POOL_RELEASE_CHECKSUM": "reviewed-checksum",
        "PRIVATE_MEDIA_S3_BUCKET": "requested-private-bucket",
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "requested-private-access",
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "requested-private-secret",
        "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": "",
        "LETSENCRYPT_EMAIL": "ops@example.com",
    }


def _apply_log(tmp_path: Path) -> list[str]:
    return (tmp_path / "apply.log").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("previous_slots", [("web",), ("web", "web-next")])
def test_release_setup_runs_once_before_web_activation(
    tmp_path: Path, fake_bin: Path, previous_slots: tuple[str, ...]
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="success")
    (tmp_path / "docker-compose.deployment.yml").write_text(
        "services:\n" + "".join(f"  {slot}:\n    image: old-image\n" for slot in previous_slots),
        encoding="utf-8",
    )
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    expected = (
        "candidate-migrate",
        "sync_feature_flags",
        "bootstrap_photographer_group",
        "collectstatic --noinput",
    )
    indices = []
    for marker in expected:
        matches = [index for index, command in enumerate(commands) if marker in command]
        assert len(matches) == 1, (marker, matches)
        indices.append(matches[0])
    assert indices == sorted(indices)
    candidate_up = next(
        index for index, command in enumerate(commands) if " up -d --no-deps web nginx" in command
    )
    assert indices[-1] < candidate_up
    assert not any(" up -d --remove-orphans" in command for command in commands)


@pytest.mark.parametrize(
    ("scenario", "expect_prune"),
    [("success", True), ("image-prune-failure", True), ("public-failure", False)],
)
def test_unused_images_are_pruned_only_after_successful_commit(
    tmp_path: Path, fake_bin: Path, scenario: str, expect_prune: bool
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    result = _run("deploy/apply-deployment.sh", env=env)
    log = _apply_log(tmp_path)
    prune = "docker image prune -a -f"

    assert (prune in log) is expect_prune
    if scenario == "public-failure":
        assert result.returncode != 0
        assert "DEPLOY_RESULT=failure" in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert "DEPLOY_RESULT=success" in result.stdout
        expected_result = "failure" if scenario == "image-prune-failure" else "success"
        assert f"DEPLOY_IMAGE_PRUNE_RESULT={expected_result}" in result.stdout
        assert log.index(prune) > next(
            index
            for index, line in enumerate(log)
            if line.startswith("mv ") and line.endswith("/deployed-image")
        )
        if scenario == "image-prune-failure":
            assert "Unused Docker image cleanup failed" in result.stderr


def test_remote_api_requires_private_edge_before_mutation(tmp_path: Path, fake_bin: Path) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="success")
    env.pop("WORKER_POOL_PRIVATE_API_IPV4")
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    assert "Remote deployment requires" in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_web_release_clears_recovery_before_next_installer_and_release(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="success")
    env.update(
        PHOTO_WORKER_PLACEMENT="remote",
        WORKER_POOL_ACTIVATION="normal",
        PHOTO_PROCESSING_FLEET_TOKEN="fleet-test-only",
        WORKER_POOL_PRIVATE_API_IPV4="10.0.0.5",
        WORKER_POOL_RELEASE_MANIFEST="/reviewed.json",
        WORKER_POOL_RELEASE_CHECKSUM="a" * 64,
    )
    previous_remote = (
        PREVIOUS_ENV + b"PHOTO_WORKER_PLACEMENT=remote\nPHOTO_PROCESSING_ENABLED=True\n"
    )
    (tmp_path / ".env").write_bytes(previous_remote)
    (tmp_path / "previous-env.expected").write_bytes(previous_remote)
    (tmp_path / "worker-pools-current.json").write_text("{}\n")
    _write_executable(
        fake_bin / "python3",
        "exec " + sys.executable + ' "$@"',
    )
    first = _run("deploy/apply-deployment.sh", env=env)
    assert first.returncode == 0, first.stderr
    assert not (tmp_path / ".deployment-recovery").exists()

    source = (ROOT / "deploy/run-remote.sh").read_text()
    (tmp_path / "deploy/worker-pools").mkdir()
    (tmp_path / "deploy/worker-pools/release.py").write_text(
        "def require_native_compatible_release(*args): pass\n"
    )
    installer = source.split("deployment_command = r'''", 1)[1].split("'''\n", 1)[0]
    installer = installer.replace("deployment_root=/opt/photo-prjct", f"deployment_root={tmp_path}")
    _write_executable(fake_bin / "flock", "exit 0")
    checked = subprocess.run(
        ["sh", "-c", installer],
        env={**os.environ, **env, "DEPLOYMENT_ARCHIVE_NAME": "invalid"},
        text=True,
        capture_output=True,
    )
    assert checked.returncode == 2, checked.stderr  # passed pre-package gate

    env["EXPECTED_DEPLOYED_IMAGE"] = (tmp_path / "deployed-image").read_text().strip()
    (tmp_path / "previous-env.expected").write_bytes((tmp_path / ".env").read_bytes())
    second = _run("deploy/apply-deployment.sh", env=env)
    assert second.returncode == 0, second.stderr
    assert not (tmp_path / ".deployment-recovery").exists()


def test_installer_fences_unfinished_web_recovery_before_package_replacement(
    tmp_path: Path,
) -> None:
    source = (ROOT / "deploy/run-remote.sh").read_text()
    installer = source.split("deployment_command = r'''", 1)[1].split("'''\n", 1)[0]
    installer = installer.replace("deployment_root=/opt/photo-prjct", f"deployment_root={tmp_path}")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "flock", "exit 0")
    (tmp_path / "docker-compose.deployment.yml").write_text("original\n")
    (tmp_path / ".deployment-recovery").mkdir()
    result = subprocess.run(
        ["sh", "-c", installer],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOYMENT_ARCHIVE_NAME": "invalid",
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1, result.stderr
    assert "recovery remains unfinished" in result.stderr
    assert (tmp_path / "docker-compose.deployment.yml").read_text() == "original\n"


def _render_gallery_environment(env_file: Path) -> tuple[dict[str, str], str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "docker-compose.deployment.yml"),
            "config",
            "--environment",
        ],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    environment = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return environment, result.stderr


SUCCESS_PHASES = [
    "validate",
    "snapshot",
    "candidate-pull",
    "private-media-preflight",
    "migration-preflight",
    "observability-preflight",
    "observability-reconcile",
    "vector-database-preflight",
    "projection-preflight",
    "certificate",
    "compose-reconcile",
    "local-health",
    "gallery-media-smoke",
    "worker-health",
    "public-health",
    "observability-verify",
    "commit",
]


def _deployment_markers(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        re.sub(r" elapsed_seconds=\d+$", "", line)
        for line in result.stdout.splitlines()
        if line.startswith("DEPLOY_")
    ]


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


def test_apply_markers_include_elapsed_seconds(tmp_path: Path, fake_bin: Path) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    markers = [line for line in result.stdout.splitlines() if line.startswith("DEPLOY_")]
    assert markers
    assert all(
        re.fullmatch(r"DEPLOY_PHASE=[a-z-]+ elapsed_seconds=\d+", line) for line in markers[:-2]
    )
    assert markers[-2] == "DEPLOY_IMAGE_PRUNE_RESULT=success"
    assert re.fullmatch(
        r"DEPLOY_RESULT=success phase=commit rollback=not-needed elapsed_seconds=\d+",
        markers[-1],
    )


def test_successful_remote_deploy_relays_only_validated_deployment_markers(
    tmp_path: Path, fake_bin: Path
) -> None:
    secret = "remote-secret-must-not-be-relayed"
    key_file = tmp_path / "key"
    key_file.write_text("private key\n", encoding="utf-8")
    key_file.chmod(0o600)
    environment_file = tmp_path / "environment"
    environment_file.write_text(f'VM_SSH_KEY_FILE="{key_file}"\n', encoding="utf-8")
    environment_file.chmod(0o600)
    remote_values = (
        (ROOT / "deploy/run-remote.sh")
        .read_text(encoding="utf-8")
        .split("REMOTE_DEPLOYMENT_VALUES='", maxsplit=1)[1]
        .split("'\n\nrun_public_monitor", maxsplit=1)[0]
        .split()
    )
    _write_executable(fake_bin / "scp", "exit 0")
    _write_executable(
        fake_bin / "ssh",
        f"""
cat >/dev/null
printf '%s\\n' \\
  '{secret}' \\
  'DEPLOY_PHASE=validate elapsed_seconds=1' \\
  'DEPLOY_PHASE=commit elapsed_seconds=2 unexpected=value' \\
  'DEPLOY_IMAGE_PRUNE_RESULT=failure injected=value' \\
  'DEPLOY_IMAGE_PRUNE_RESULT=failure' \\
  'DEPLOY_RESULT=success phase=commit rollback=not-needed elapsed_seconds=3'
""",
    )
    environment = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FINDME_ENV_FILE": str(environment_file),
        "VM_HOST": "example.test",
        "VM_USER": "deployer",
        "VM_SSH_KNOWN_HOSTS": "example.test ssh-ed25519 AAAA",
        **{name: "value" for name in remote_values},
    }

    result = subprocess.run(
        ["sh", ROOT / "deploy/run-remote.sh", "deploy"],
        env={**os.environ, **environment},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "DEPLOY_PHASE=validate elapsed_seconds=1",
        "DEPLOY_IMAGE_PRUNE_RESULT=failure",
        "DEPLOY_RESULT=success phase=commit rollback=not-needed elapsed_seconds=3",
        "[remote] stage=deploy status=ok",
    ]
    assert secret not in result.stdout


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


def test_dark_deploy_persists_empty_gallery_signing_values_without_delivery_contact(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The off-state package must apply before any CDN or image origin exists."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        GALLERY_CDN_ORIGIN="https://img.findme-photo.ru",
        GALLERY_CDN_TOKEN_SECRET="",
        GALLERY_IMGPROXY_KEY="",
        GALLERY_IMGPROXY_SALT="",
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    rendered, render_stderr = _render_gallery_environment(tmp_path / ".env")
    assert {
        name: rendered[name]
        for name in (
            "GALLERY_CDN_ORIGIN",
            "GALLERY_CDN_TOKEN_SECRET",
            "GALLERY_IMGPROXY_KEY",
            "GALLERY_IMGPROXY_SALT",
        )
    } == {
        "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
        "GALLERY_CDN_TOKEN_SECRET": "",
        "GALLERY_IMGPROXY_KEY": "",
        "GALLERY_IMGPROXY_SALT": "",
    }
    rendered_activity = "\n".join(
        (result.stdout, result.stderr, render_stderr, *_apply_log(tmp_path))
    )
    assert "img.findme-photo.ru" not in rendered_activity
    assert "imgproxy" not in rendered_activity.lower()


@pytest.mark.parametrize(
    ("cdn_token_secret", "imgproxy_key", "imgproxy_salt", "token_fragment"),
    [
        pytest.param("trailLEAK\\", "11" * 32, "22" * 32, "trailLEAK", id="trailing-backslash"),
        pytest.param(
            r"pairLEAK\\slashes",
            "11" * 32,
            "22" * 32,
            "pairLEAK",
            id="consecutive-backslashes",
        ),
        pytest.param(
            r"quoteLEAK\'tail",
            "11" * 32,
            "22" * 32,
            "quoteLEAK",
            id="backslash-before-apostrophe",
        ),
        pytest.param(
            'doubleLEAK"tail',
            "11" * 32,
            "22" * 32,
            "doubleLEAK",
            id="double-quote",
        ),
        pytest.param("dollarLEAK$TASK5", "11" * 32, "22" * 32, "TASK5", id="dollar-sign"),
        pytest.param("hashLEAK#tail", "11" * 32, "22" * 32, "hashLEAK", id="hash-sign"),
        pytest.param("", "", "", None, id="empty-optional-values"),
    ],
)
def test_apply_and_recovery_preserve_literal_gallery_values_without_disclosure(
    tmp_path: Path,
    cdn_token_secret: str,
    imgproxy_key: str,
    imgproxy_salt: str,
    token_fragment: str | None,
) -> None:
    gallery_values = {
        "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
        "GALLERY_CDN_TOKEN_SECRET": cdn_token_secret,
        "GALLERY_IMGPROXY_KEY": imgproxy_key,
        "GALLERY_IMGPROXY_SALT": imgproxy_salt,
    }
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    candidate_bin = tmp_path / "candidate-bin"
    candidate_bin.mkdir()
    env = _apply_env(candidate_root, candidate_bin, scenario="private-media-no-photo")
    env.update(gallery_values)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    rendered, render_stderr = _render_gallery_environment(candidate_root / ".env")
    assert {name: rendered[name] for name in gallery_values} == gallery_values
    if cdn_token_secret:
        assert cdn_token_secret not in render_stderr
        assert cdn_token_secret not in (
            result.stdout + result.stderr + "\n".join(_apply_log(candidate_root))
        )
    if token_fragment:
        assert token_fragment not in render_stderr
        assert token_fragment not in result.stdout + result.stderr + "\n".join(
            _apply_log(candidate_root)
        )

    rollback_root = tmp_path / "rollback"
    rollback_root.mkdir()
    rollback_bin = tmp_path / "rollback-bin"
    rollback_bin.mkdir()
    previous_env = (candidate_root / ".env").read_bytes()
    rollback_env = _apply_env(rollback_root, rollback_bin, scenario="certificate-failure")
    (rollback_root / ".env").write_bytes(previous_env)
    (rollback_root / "previous-env.expected").write_bytes(previous_env)
    rollback_env.update(
        {
            "GALLERY_CDN_TOKEN_SECRET": "replacement-token",
            "GALLERY_IMGPROXY_KEY": "33" * 32,
            "GALLERY_IMGPROXY_SALT": "44" * 32,
            "REAL_DOCKER": shutil.which("docker") or "docker",
            "REAL_DEPLOYMENT_COMPOSE": str(ROOT / "docker-compose.deployment.yml"),
            "RECOVERY_COMPOSE_CONFIG": str(rollback_root / "recovery-compose.environment"),
            "RECOVERY_COMPOSE_STDERR": str(rollback_root / "recovery-compose.stderr"),
        }
    )

    rollback = _run("deploy/apply-deployment.sh", env=rollback_env)

    assert rollback.returncode != 0
    assert "DEPLOY_RESULT=failure phase=certificate rollback=succeeded" in rollback.stdout
    assert (rollback_root / ".env").read_bytes() == previous_env
    recovery_environment = dict(
        line.split("=", 1)
        for line in (rollback_root / "recovery-compose.environment")
        .read_text(encoding="utf-8")
        .splitlines()
        if "=" in line
    )
    assert {name: recovery_environment[name] for name in gallery_values} == gallery_values
    recovery_stderr = (rollback_root / "recovery-compose.stderr").read_text(encoding="utf-8")
    if cdn_token_secret:
        assert cdn_token_secret not in recovery_stderr
    if token_fragment:
        assert token_fragment not in recovery_stderr
    assert "replacement-token" not in recovery_stderr
    assert "replacement-token" not in rollback.stdout + rollback.stderr + "\n".join(
        _apply_log(rollback_root)
    )


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://img.findme-photo.ru",
        "https://img.findme-photo.ru/",
        "https://another.example",
    ],
)
def test_apply_rejects_noncanonical_gallery_origin_before_mutation(
    tmp_path: Path, fake_bin: Path, origin: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["GALLERY_CDN_ORIGIN"] = origin

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "GALLERY_CDN_ORIGIN must be https://img.findme-photo.ru" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_apply_persists_the_stable_gunicorn_profile(tmp_path: Path, fake_bin: Path) -> None:
    """The candidate environment must carry the web process bound into the container."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "0",
            "GUNICORN_MAX_REQUESTS_JITTER": "0",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "GUNICORN_WORKERS=5" in deployed_env
    assert "GUNICORN_THREADS=2" in deployed_env
    assert "GUNICORN_TIMEOUT=180" in deployed_env
    assert "GUNICORN_MAX_REQUESTS=0" in deployed_env
    assert "GUNICORN_MAX_REQUESTS_JITTER=0" in deployed_env


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
            "GUNICORN_MAX_REQUESTS": "0",
            "GUNICORN_MAX_REQUESTS_JITTER": "0",
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
    [("missing-secret-key", "Set SECRET_KEY")],
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
    assert re.fullmatch(
        r"DEPLOY_RESULT=failure phase=validate rollback=not-needed elapsed_seconds=\d+",
        [line for line in result.stdout.splitlines() if line.startswith("DEPLOY_RESULT=")][-1],
    )
    output = f"{result.stdout}\n{result.stderr}"
    assert "validate-db-secret-must-not-appear" not in output
    assert "validate-object-secret-must-not-appear" not in output
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_entrypoint_runs_gunicorn_with_the_stable_profile(tmp_path: Path, fake_bin: Path) -> None:
    """The running web process must keep workers warm without request-count recycling."""
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
            "GUNICORN_MAX_REQUESTS": "0",
            "GUNICORN_MAX_REQUESTS_JITTER": "0",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gunicorn.log").read_text(encoding="utf-8") == (
        "config.wsgi:application --config python:config.gunicorn --bind 0.0.0.0:8000 "
        "--workers 5 --threads 2 "
        "--timeout 180 --max-requests 0 --max-requests-jitter 0\n"
    )


def test_web_slots_have_equivalent_application_configuration() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.deployment.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env={**os.environ, "APP_IMAGE": "test-release"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    import json

    services = json.loads(result.stdout)["services"]
    web = services["web"]
    next_web = services["web-next"]
    for key in ("image", "environment", "depends_on", "healthcheck", "expose", "restart"):
        assert next_web[key] == web[key]
    assert next_web["logging"]["options"]["tag"] == "findme.service=web-next"
    assert web["logging"]["options"]["tag"] == "findme.service=web"


def test_entrypoint_starts_gunicorn_without_release_setup(tmp_path: Path, fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "python",
        """
printf 'python %s\\n' "$*" >> "$COMMAND_LOG"
case " $* " in
  *" sync_feature_flags "*) exit 23 ;;
esac
""",
    )
    _write_executable(fake_bin / "gunicorn", 'printf "gunicorn %s\\n" "$*" >> "$COMMAND_LOG"')

    result = subprocess.run(
        ["sh", ROOT / "src/backend/entrypoint.sh"],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(tmp_path / "commands.log"),
            "GUNICORN_WORKERS": "5",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "180",
            "GUNICORN_MAX_REQUESTS": "0",
            "GUNICORN_MAX_REQUESTS_JITTER": "0",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert len((tmp_path / "commands.log").read_text(encoding="utf-8").splitlines()) == 1
    assert (tmp_path / "commands.log").read_text(encoding="utf-8").startswith("gunicorn ")


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
            "GUNICORN_MAX_REQUESTS": "0",
            "GUNICORN_MAX_REQUESTS_JITTER": "0",
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
            "--max-requests 0 --max-requests-jitter 0"
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
            "GUNICORN_MAX_REQUESTS": "0",
            "GUNICORN_MAX_REQUESTS_JITTER": "0",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gunicorn.log").read_text(encoding="utf-8") == "multiproc=\n"


def test_missing_processing_prerequisite_prevents_deployment(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The always-available selfie page cannot deploy without its processor."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["PHOTO_PROCESSING_ENABLED"] = "False"
    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert (
        "Remote deployment requires enabled API, fleet credential and private edge" in result.stderr
    )
    assert not (tmp_path / "apply.log").exists()


def test_deployment_rejects_test_commerce_adapters_before_any_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["COMMERCE_PAYMENT_GATEWAY_FACTORY"] = (
        "commerce.test_payment_gateway.DeterministicPaymentGateway"
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "must not select a local/test adapter" in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_deployment_rejects_non_postbox_commerce_email_factory_before_any_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(_real_commerce_worker_settings())
    env["COMMERCE_EMAIL_SENDER_FACTORY"] = "commerce.smtp_email_sender.smtp_email_sender_factory"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "COMMERCE_EMAIL_SENDER_FACTORY must use the Yandex Postbox adapter" in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_disabled_commerce_worker_is_absent_from_the_deployment_profile(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path))
    assert "--profile commerce up -d" not in commands
    assert "--profile commerce rm -sf commerce-worker" in commands
    environment = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "COMMERCE_WORKER_ENABLED=False" in environment
    assert "COMMERCE_PAYMENT_GATEWAY_FACTORY=\n" in environment


def test_enabled_commerce_worker_requires_real_settings_before_any_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["COMMERCE_WORKER_ENABLED"] = "True"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "Set COMMERCE_PUBLIC_ORIGIN" in result.stderr
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize(
    "origin",
    [
        "https://findme-photo.ru/",
        "https://findme-photo.ru/orders",
        "https://findme-photo.ru?continue=https://evil.example",
        "https://findme-photo.ru#fragment",
        "https://user@findme-photo.ru",
        "https://findme-photo.ru:443",
        "https://evil.example",
        " https://findme-photo.ru",
        "https://findme-photo.ru ",
    ],
)
def test_enabled_commerce_worker_requires_canonical_public_origin_before_mutation(
    tmp_path: Path, fake_bin: Path, origin: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(_real_commerce_worker_settings())
    env["COMMERCE_PUBLIC_ORIGIN"] = origin

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "COMMERCE_PUBLIC_ORIGIN must be https://findme-photo.ru" in result.stderr
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize(
    ("missing_name", "message"),
    [
        ("COMMERCE_PUBLIC_ORIGIN", "Set COMMERCE_PUBLIC_ORIGIN"),
        ("COMMERCE_PAYMENT_GATEWAY_FACTORY", "Set COMMERCE_PAYMENT_GATEWAY_FACTORY"),
        ("COMMERCE_EMAIL_SENDER_FACTORY", "Set COMMERCE_EMAIL_SENDER_FACTORY"),
        ("COMMERCE_WORKER_FACTORY", "Set COMMERCE_WORKER_FACTORY"),
        ("COMMERCE_EMAIL_FROM_ADDRESS", "Set COMMERCE_EMAIL_FROM_ADDRESS"),
        ("COMMERCE_POSTBOX_API_KEY_ID", "Set COMMERCE_POSTBOX_API_KEY_ID"),
        ("COMMERCE_POSTBOX_API_KEY_SECRET", "Set COMMERCE_POSTBOX_API_KEY_SECRET"),
        (
            "COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
            "Set COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
        ),
        ("COMMERCE_SUPPORT_CONTACT", "Set COMMERCE_SUPPORT_CONTACT"),
    ],
)
def test_enabled_commerce_worker_requires_complete_postbox_configuration_before_mutation(
    tmp_path: Path, fake_bin: Path, missing_name: str, message: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(_real_commerce_worker_settings())
    env[missing_name] = ""

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert message in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_enabled_commerce_worker_persists_postbox_settings_without_logging_secret(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="commerce-worker-ready-after-retry")
    env.update(_real_commerce_worker_settings())

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "COMMERCE_PUBLIC_ORIGIN=https://findme-photo.ru" in deployed_env
    assert (
        "COMMERCE_PAYMENT_GATEWAY_FACTORY="
        "commerce.payment_simulator.payment_simulator_gateway_factory" in deployed_env
    )
    assert (
        "COMMERCE_EMAIL_SENDER_FACTORY="
        "commerce.postbox_email_sender.postbox_email_sender_factory" in deployed_env
    )
    assert "COMMERCE_WORKER_FACTORY=commerce.runtime.commerce_worker_factory" in deployed_env
    assert "COMMERCE_EMAIL_FROM_ADDRESS=orders@findme-photo.ru" in deployed_env
    assert "COMMERCE_POSTBOX_API_KEY_ID=postbox-api-key-id" in deployed_env
    assert "COMMERCE_POSTBOX_API_KEY_SECRET=postbox-secret-must-not-be-logged" in deployed_env
    assert "COMMERCE_SUPPORT_CONTACT=support@findme-photo.ru" in deployed_env
    assert "postbox-secret-must-not-be-logged" not in result.stdout
    assert "postbox-secret-must-not-be-logged" not in result.stderr
    assert "postbox-secret-must-not-be-logged" not in "\n".join(_apply_log(tmp_path))


def test_enabled_commerce_worker_retries_readiness_until_its_lock_is_live(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="commerce-worker-ready-after-retry")
    env.update(_real_commerce_worker_settings())

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "commerce-health-attempts").read_text(encoding="utf-8") == "3\n"
    assert "Commerce worker readiness check attempt 1 failed; retrying" in result.stderr
    assert "Commerce worker readiness check attempt 2 failed; retrying" in result.stderr
    assert "DEPLOY_RESULT=success" in result.stdout
    commands = "\n".join(_apply_log(tmp_path))
    assert "--profile commerce up -d --no-deps commerce-worker" in commands
    assert commands.count("commerce-worker-health-attempt=") == 3


def test_enabled_commerce_worker_readiness_exhaustion_requires_forward_recovery(
    tmp_path: Path, fake_bin: Path
) -> None:
    previous_env = PREVIOUS_ENV + b"".join(
        f"{key}={value}\n".encode() for key, value in _real_commerce_worker_settings().items()
    )
    env = _apply_env(tmp_path, fake_bin, scenario="commerce-worker-health-failure")
    env.update(_real_commerce_worker_settings())
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Commerce worker readiness exhausted after 6 attempts" in result.stderr
    assert (tmp_path / ".env").read_bytes() != previous_env
    assert (tmp_path / ".deployment-recovery/candidate.env").read_bytes() == (
        tmp_path / ".env"
    ).read_bytes()
    assert (tmp_path / "commerce-health-attempts").read_text(encoding="utf-8") == "6\n"
    commands = "\n".join(_apply_log(tmp_path))
    assert "previous-web-processing-schema-probe" not in commands
    assert " stop web" in commands
    assert "--profile commerce up -d --no-deps commerce-worker" in commands
    assert "DEPLOY_RESULT=failure phase=worker-health rollback=failed" in result.stdout


def test_missing_face_embedding_prerequisite_preserves_existing_deployment(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A failed prerequisite check cannot mutate an existing deployment."""
    previous_env = PREVIOUS_ENV + (
        b"WORKER_IMAGE=old-worker-image\n"
        b"PHOTO_PROCESSING_ENABLED=True\n"
        b"PHOTO_PROCESSING_WORKER_TOKEN=old-worker-token\n"
        b"PHOTO_WORKER_REPLICAS=2\n"
    )
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["PHOTO_PROCESSING_FACE_ENABLED"] = "False"
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert (tmp_path / ".env").read_bytes() == previous_env
    assert not (tmp_path / "apply.log").exists()


def test_missing_processing_prerequisite_does_not_reconcile_worker_profile(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The validation failure occurs before worker reconciliation."""
    env = _apply_env(tmp_path, fake_bin, scenario="worker-removal-failure")
    env["PHOTO_PROCESSING_ENABLED"] = "False"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_selfie_prerequisites_are_required_for_every_deployment(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["PHOTO_PROCESSING_ENABLED"] = "False"

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert (
        "Remote deployment requires enabled API, fleet credential and private edge" in result.stderr
    )
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV


def test_preview_first_activation_accepts_and_persists_current_worker_identities(
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
            "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES": (
                "1/capture_metadata/2,2/generate_preview/1,"
                "2/generate_watermarked_preview/1,"
                "3/face_embedding/5,1/bib_recognition/1"
            ),
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "PHOTO_PROCESSING_PREVIEW_ENABLED=True" in deployed_env
    assert "PHOTO_PROCESSING_FACE_ENABLED=True" in deployed_env
    assert (
        "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES=1/capture_metadata/2,"
        "2/generate_preview/1,2/generate_watermarked_preview/1,"
        "3/face_embedding/5,1/bib_recognition/1" in deployed_env
    )


def test_deployment_default_worker_identities_are_disjoint_and_complete(
    tmp_path: Path, fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty configured value must not inherit an ambient worker identity."""
    monkeypatch.setenv("PHOTO_WORKER_PROCESSOR_IDENTITIES", "9/obsolete/9")
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_PROCESSING_PREVIEW_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
        }
    )
    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    persisted_default = next(
        line for line in deployed_env if line.startswith("PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES=")
    )
    default_identity_line = (
        "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES=1/capture_metadata/2,"
        "2/generate_preview/1,2/generate_watermarked_preview/1,"
        "3/face_embedding/5,1/bib_recognition/1"
    )
    assert persisted_default == default_identity_line
    assert "selfie_query" not in persisted_default
    assert "PHOTO_WORKER_SELFIE_PROCESSOR_IDENTITIES=1/selfie_query/2" in deployed_env


def test_deployment_persists_the_fixed_watermarked_preview_bulk_identity(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The packaged worker may accept the future identity without activating its policy."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_PROCESSING_PREVIEW_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    deployed_env = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert (
        "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES=1/capture_metadata/2,"
        "2/generate_preview/1,2/generate_watermarked_preview/1,"
        "3/face_embedding/5,1/bib_recognition/1" in deployed_env
    )


@pytest.mark.parametrize(
    "identities",
    [
        "1/selfie_query/1",
        "1/capture_metadata/1,2/generate_preview/1,2/face_embedding/3",
        "1/capture_metadata/2,2/generate_preview/1,2/face_embedding/3,9/bogus/9",
        "1/capture_metadata/2,2/generate_preview/1,2/generate_preview/1,2/face_embedding/3",
    ],
)
def test_deployment_rejects_worker_identity_lists_the_worker_would_not_accept(
    tmp_path: Path, fake_bin: Path, identities: str
) -> None:
    """Deployment must reject invalid identities before mutating the live environment."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES": identities,
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES must be a unique ordered list" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"PHOTO_PROCESSING_PREVIEW_ENABLED": "True"},
            "Remote deployment requires enabled API, fleet credential and private edge",
        ),
    ],
)
def test_preview_first_activation_rejects_partial_or_implicit_configuration(
    tmp_path: Path, fake_bin: Path, overrides: dict[str, str], message: str
) -> None:
    """Preview and face work must be a conscious operator activation, not an image side effect."""
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["PHOTO_PROCESSING_ENABLED"] = "False"
    env.update(overrides)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert message in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV


@pytest.mark.parametrize(
    "missing_identity",
    (
        "1/capture_metadata/2",
        "2/generate_preview/1",
        "2/generate_watermarked_preview/1",
        "3/face_embedding/5",
        "1/bib_recognition/1",
    ),
)
def test_preview_activation_requires_every_approved_photo_identity_before_mutation(
    tmp_path: Path, fake_bin: Path, missing_identity: str
) -> None:
    required_identities = (
        "1/capture_metadata/2",
        "2/generate_preview/1",
        "2/generate_watermarked_preview/1",
        "3/face_embedding/5",
        "1/bib_recognition/1",
    )
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        {
            "PHOTO_PROCESSING_ENABLED": "True",
            "WORKER_IMAGE": "worker-image",
            "PHOTO_PROCESSING_WORKER_TOKEN": "worker-token",
            "PHOTO_PROCESSING_PREVIEW_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
            "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES": ",".join(
                identity for identity in required_identities if identity != missing_identity
            ),
        }
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert (
        f"PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES must include {missing_identity}" in result.stderr
    )
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"PHOTO_PROCESSING_ENABLED": "true"},
            "Remote deployment requires enabled API, fleet credential and private edge",
        ),
        (
            {"PHOTO_PROCESSING_FACE_ENABLED": "true"},
            "PHOTO_PROCESSING_FACE_ENABLED must be True or False",
        ),
        (
            {"PHOTO_WORKER_BULK_PROCESSOR_TYPES": "capture_metadata"},
            (
                "PHOTO_WORKER_BULK_PROCESSOR_TYPES must be bib_recognition,face_embedding,"
                "capture_metadata,generate_preview,generate_watermarked_preview"
            ),
        ),
        (
            {
                "PHOTO_PROCESSING_ENABLED": "True",
                "WORKER_IMAGE": "worker-image",
            },
            "Remote deployment requires enabled API, fleet credential and private edge",
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
    if (
        message == "Remote deployment requires enabled API, fleet credential and private edge"
        and overrides.get("WORKER_IMAGE")
    ):
        env.pop("PHOTO_PROCESSING_FLEET_TOKEN")
    env.update(overrides)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 2
    assert message in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


def test_successful_deployment_installs_cart_cleanup_only_after_the_candidate_commits(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="private-media-no-photo"),
    )

    assert result.returncode == 0, result.stderr
    assert "Installed daily cart cleanup" in result.stdout
    assert result.stdout.index("DEPLOY_PHASE=commit") < result.stdout.index(
        "Installed daily cart cleanup"
    )
    commands = _apply_log(tmp_path)
    assert any(command.startswith("crontab ") for command in commands)
    assert "paid-photo-cart" not in "\n".join(commands)


def test_failed_candidate_without_prior_cart_cleanup_leaves_schedule_absent(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="health-failure"),
    )

    assert result.returncode != 0
    assert not (tmp_path / "crontab").exists()
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"


def test_failed_candidate_keeps_a_preexisting_cart_cleanup_schedule(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="health-failure")
    crontab_state = Path(env["CRONTAB_STATE"])
    crontab_state.write_text(_cart_cleanup_block(tmp_path), encoding="utf-8")

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    restored_schedule = crontab_state.read_text(encoding="utf-8")
    assert restored_schedule.count("# BEGIN photo-prjct-cart-cleanup") == 1
    assert _cart_cleanup_block(tmp_path) in restored_schedule
    assert restored_schedule == _cart_cleanup_block(tmp_path)


@pytest.mark.parametrize("missing_command", ["crontab", "flock"])
def test_uploads_off_requires_cart_cleanup_host_tools_before_mutation(
    tmp_path: Path, fake_bin: Path, missing_command: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    (fake_bin / missing_command).unlink()
    _write_executable(fake_bin / "date", "printf '0\\n'")
    _write_executable(fake_bin / ({"crontab", "flock"} - {missing_command}).pop(), ":")
    env["PATH"] = str(fake_bin)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 1
    assert f"{missing_command} is required for cart cleanup" in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


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
        "DEPLOY_IMAGE_PRUNE_RESULT=success",
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


def test_deployment_avoids_full_corpus_projection_work_on_the_live_database(
    tmp_path: Path,
    fake_bin: Path,
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert _deployment_markers(result) == [
        *(f"DEPLOY_PHASE={phase}" for phase in SUCCESS_PHASES),
        "DEPLOY_IMAGE_PRUNE_RESULT=success",
        "DEPLOY_RESULT=success phase=commit rollback=not-needed",
    ]
    commands = _apply_log(tmp_path)
    assert commands.count("volume-inspect photo-prjct_pgdata") == 1
    assert any(" pull web" in command for command in commands)
    assert "candidate-migration-history" in commands
    assert "candidate-migration-plan" in commands
    assert "unexpected-fresh-migration-history" not in commands
    assert "candidate-projection-report" not in commands
    assert "candidate-projection-benchmark" not in commands
    assert any(" stop nginx" in command for command in commands)
    assert any(" up -d --no-deps web nginx" in command for command in commands)
    _assert_no_env_temporary_files(tmp_path)


def _projection_cutover_env(tmp_path: Path, fake_bin: Path, *, scenario: str) -> dict[str, str]:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    previous_env = PREVIOUS_ENV + (
        b"PHOTO_PROCESSING_ENABLED=True\nPHOTO_WORKER_REPLICAS=1\nCOMMERCE_WORKER_ENABLED=False\n"
    )
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)
    return env


def test_gallery_projection_cutover_preserves_old_web_until_clean_candidate_reconciliation(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A candidate reader must not start before its projection is rebuilt and clean."""
    result = _run(
        "deploy/apply-deployment.sh",
        env=_projection_cutover_env(tmp_path, fake_bin, scenario="gallery-projection-success"),
    )

    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    candidate_pull = next(
        index
        for index, command in enumerate(commands)
        if " pull web" in command and "APP_IMAGE=new-image" in command
    )
    migrate = commands.index("candidate-migrate")
    drain = commands.index("candidate-gallery-publication-drain")
    rebuild = commands.index("candidate-gallery-projection-rebuild")
    verify = commands.index("candidate-gallery-projection-verify")
    candidate_up = next(
        index
        for index, command in enumerate(commands)
        if " up -d --no-deps web nginx" in command and "APP_IMAGE=new-image" in command
    )
    smoke = commands.index("candidate-gallery-projection-smoke")
    worker_health = next(
        index
        for index, command in enumerate(commands)
        if "findme-worker-pool-metrics verify" in command
    )

    assert candidate_pull < migrate < drain < rebuild < verify < candidate_up < smoke
    assert smoke < worker_health
    assert not any(
        " stop web" in command or " stop nginx" in command for command in commands[: verify + 1]
    )
    assert not any(
        " up -d --no-deps web nginx" in command and "APP_IMAGE=new-image" in command
        for command in commands[: verify + 1]
    )
    assert not any("commerce-worker" in command and " stop " in command for command in commands)
    assert not any("import-worker" in command and " stop " in command for command in commands)


def test_gallery_projection_preparation_runs_candidate_drain_before_rebuild(
    tmp_path: Path, fake_bin: Path
) -> None:
    """The deploy must invoke the real drain command in the worker-paused preparation window."""
    result = _run(
        "deploy/apply-deployment.sh",
        env=_projection_cutover_env(tmp_path, fake_bin, scenario="gallery-projection-success"),
    )

    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    migrate = commands.index("candidate-migrate")
    drain = commands.index("candidate-gallery-publication-drain")
    rebuild = commands.index("candidate-gallery-projection-rebuild")

    assert migrate < drain < rebuild


@pytest.mark.parametrize(
    ("scenario", "expected_phase", "last_pre_failure_command"),
    [
        (
            "gallery-projection-publication-drain-failure",
            "projection-preflight",
            "candidate-gallery-publication-drain",
        ),
        ("gallery-projection-migration-failure", "projection-preflight", "candidate-migrate"),
        (
            "gallery-projection-rebuild-failure",
            "projection-preflight",
            "candidate-gallery-projection-rebuild",
        ),
        (
            "gallery-projection-verification-failure",
            "projection-preflight",
            "candidate-gallery-projection-verify",
        ),
        (
            "gallery-projection-smoke-failure",
            "gallery-media-smoke",
            "candidate-gallery-projection-smoke",
        ),
    ],
)
def test_gallery_projection_cutover_failures_recover_previous_worker_topology_without_leaks(
    tmp_path: Path,
    fake_bin: Path,
    scenario: str,
    expected_phase: str,
    last_pre_failure_command: str | None,
) -> None:
    """Every mutated cutover failure must use one redacted prior-package recovery path."""
    result = _run(
        "deploy/apply-deployment.sh",
        env=_projection_cutover_env(tmp_path, fake_bin, scenario=scenario),
    )

    assert result.returncode != 0
    rollback = "failed" if expected_phase == "gallery-media-smoke" else "succeeded"
    assert f"DEPLOY_RESULT=failure phase={expected_phase} rollback={rollback}" in result.stdout
    commands = _apply_log(tmp_path)
    if last_pre_failure_command is not None:
        assert last_pre_failure_command in commands
    old_restarted = any(
        "up -d --no-deps web nginx" in command and "APP_IMAGE=unset" in command
        for command in commands
    )
    assert old_restarted is (expected_phase != "gallery-media-smoke")
    if scenario == "gallery-projection-verification-failure":
        assert not any(
            " up -d --no-deps web nginx" in command and "APP_IMAGE=new-image" in command
            for command in commands
        )
    combined_output = result.stdout + result.stderr
    assert "private-key-must-not-reach-output" not in combined_output
    assert "photo-id-must-not-reach-output" not in combined_output


def _fresh_projection_failure_env(
    tmp_path: Path, fake_bin: Path, *, scenario: str
) -> dict[str, str]:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    for name in (".env", "deployed-image"):
        (tmp_path / name).unlink()
    env["EXPECT_CANONICAL_ENV"] = "absent"
    previous_package = tmp_path / "previous-package"
    previous_package.mkdir()
    (previous_package / "docker-compose.deployment.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n  web:\n    image: previous-package\n",
        encoding="utf-8",
    )
    (previous_package / "docker-compose.https.yml").write_text(
        "services:\n  nginx:\n    image: nginx:previous\n", encoding="utf-8"
    )
    shutil.copytree(tmp_path / "deploy", previous_package / "deploy")
    (previous_package / "deploy" / "package-version").write_text(
        "previous-package\n", encoding="utf-8"
    )
    env["PREVIOUS_DEPLOYMENT_PACKAGE_ROOT"] = str(previous_package)
    return env


def test_retained_postgres_volume_alone_forces_migration_preflight(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="migration-history-database-unavailable")
    for name in (".env", "deployed-image"):
        (tmp_path / name).unlink()
    env["EXPECT_CANONICAL_ENV"] = "absent"
    volume_state = tmp_path / ".docker-volume-photo-prjct_pgdata"
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
    assert commands.count("volume-inspect photo-prjct_pgdata") == 1
    assert (
        commands.index("volume-inspect photo-prjct_pgdata")
        < next(index for index, command in enumerate(commands) if " pull web" in command)
        < commands.index("candidate-migration-history database-unavailable")
    )
    assert "candidate-migration-plan" not in commands
    assert volume_state.read_text(encoding="utf-8") == "retained\n"
    for name in (".env", "deployed-image"):
        assert not (tmp_path / name).exists()
    assert not any("observability-" in command for command in commands)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --no-deps web nginx" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    _assert_no_env_temporary_files(tmp_path)


def test_postgres_volume_inspection_error_fails_safely_before_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="volume-inspection-error")
    for name in (".env", "deployed-image"):
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
    assert _apply_log(tmp_path) == ["volume-inspect photo-prjct_pgdata"]
    for name in (".env", "deployed-image"):
        assert not (tmp_path / name).exists()
    _assert_no_env_temporary_files(tmp_path)


def test_failed_first_deployment_after_metadata_markers_restores_no_env_state(
    tmp_path: Path, fake_bin: Path
) -> None:
    """A late failed initial rollout removes the metadata that it created before failing."""
    env = _apply_env(tmp_path, fake_bin, scenario="fresh-first-marker-failure")
    for name in (".env", "deployed-image"):
        (tmp_path / name).unlink()

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    for name in (".env", "deployed-image"):
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
        if " up -d --no-deps web nginx" in command and "APP_IMAGE=new-image" in command
    )
    assert candidate_pull < candidate_run < stop_nginx < candidate_up


@pytest.mark.parametrize(
    ("scenario", "sensitive_detail"),
    [
        ("private-media-failure", "private failure detail"),
        ("private-media-db-failure", "database unavailable detail"),
    ],
)
def test_failed_candidate_private_media_preflight_leaves_canonical_env_untouched(
    tmp_path: Path, fake_bin: Path, scenario: str, sensitive_detail: str
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
    assert sensitive_detail not in result.stdout + result.stderr
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert _env_metadata(tmp_path / ".env") == previous_metadata
    commands = _apply_log(tmp_path)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --no-deps web nginx" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    assert commands.count("candidate-requested-env-with-canonical-untouched") == 2
    _assert_no_env_temporary_files(tmp_path)


def test_failed_candidate_migration_history_stops_before_any_deployment_mutation(
    tmp_path: Path, fake_bin: Path
) -> None:
    scenario = "migration-history-missing"
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
    assert not any(" up -d --no-deps web nginx" in command for command in commands)
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
    durable_signals = (".env", "deployed-image")
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
    assert not any(" up -d --no-deps web nginx" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize(
    ("scenario", "rollback"),
    [("compose-failure", "failed"), ("recovery-failure", "failed")],
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
    assert re.fullmatch(
        rf"DEPLOY_RESULT=failure phase=compose-reconcile rollback={rollback} elapsed_seconds=\d+",
        [line for line in result.stdout.splitlines() if line.startswith("DEPLOY_RESULT=")][-1],
    )
    assert (tmp_path / ".env").read_bytes() != PREVIOUS_ENV
    assert (tmp_path / ".deployment-recovery/candidate.env").read_bytes() == (
        tmp_path / ".env"
    ).read_bytes()
    commands = _apply_log(tmp_path)
    assert "observability-install" in commands
    assert "observability-rollback" in commands
    assert "original snapshot retained" in result.stderr
    assert "previous-web-processing-schema-probe" not in commands
    assert any(" stop web" in command for command in commands)


@pytest.mark.parametrize(
    "scenario",
    [
        "processing-schema-migrate-failure",
        "processing-schema-drain-failure",
        "processing-schema-health-failure",
    ],
)
def test_dropped_processing_column_blocks_old_web_recovery_and_preserves_candidate(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    env["PHOTO_PROCESSING_FLEET_TOKEN"] = "fleet-test-only"
    previous_package = tmp_path / "previous-package"
    (previous_package / "deploy").mkdir(parents=True)
    for name in ("docker-compose.deployment.yml", "docker-compose.https.yml"):
        (previous_package / name).write_text("old package\n")
    (previous_package / "deploy/old-package").write_text("old source\n")
    env["PREVIOUS_DEPLOYMENT_PACKAGE_ROOT"] = str(previous_package)
    candidate_compose = (tmp_path / "docker-compose.deployment.yml").read_bytes()

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "rollback=failed" in result.stdout
    commands = _apply_log(tmp_path)
    if scenario == "processing-schema-health-failure":
        assert "original snapshot retained" in result.stderr
        assert "previous-web-processing-schema-probe" not in commands
    else:
        assert "Previous web is incompatible with the current processing schema" in result.stderr
        assert "previous-web-processing-schema-probe" in commands
    assert any(" stop web" in command for command in commands)
    assert not any(
        "up -d --no-deps web nginx" in command and "APP_IMAGE=unset" in command
        for command in commands
    )
    assert (tmp_path / "docker-compose.deployment.yml").read_bytes() == candidate_compose
    assert (previous_package / "deploy/old-package").is_file()
    recovery = tmp_path / ".deployment-recovery"
    assert (recovery / "previous.env").read_bytes() == PREVIOUS_ENV
    assert (recovery / "package-path").read_text().strip() == str(previous_package)
    candidate_env = recovery / "candidate.env"
    assert candidate_env.stat().st_mode & 0o777 == 0o600
    assert "APP_IMAGE=new-image\n" in candidate_env.read_text()
    assert 'PHOTO_PROCESSING_FLEET_TOKEN="fleet-test-only"\n' in candidate_env.read_text()
    if scenario == "processing-schema-health-failure":
        assert (tmp_path / ".env").read_bytes() == candidate_env.read_bytes()
    else:
        assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert (tmp_path / "deployed-image").read_text().strip() == "old-image"
    assert "private-db-detail-must-not-reach-output" not in result.stdout + result.stderr
    assert "fleet-test-only" not in result.stdout + result.stderr
    _assert_no_env_temporary_files(tmp_path)


def test_schema_compatible_web_recovers_only_after_read_only_probe(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="certificate-failure"),
    )
    assert result.returncode != 0
    assert "rollback=succeeded" in result.stdout
    commands = _apply_log(tmp_path)
    old_start = next(
        i
        for i, command in enumerate(commands)
        if "up -d --no-deps web nginx" in command and "APP_IMAGE=unset" in command
    )
    assert commands.index("previous-web-processing-schema-probe") < old_start
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / ".deployment-recovery").exists()


def _trapped_forward_recovery(tmp_path, fake_bin, *, timing, fix_sha):
    original_sha = "a" * 40
    env = _apply_env(tmp_path, fake_bin, scenario=f"processing-schema-{timing}-failure")
    env["APP_IMAGE"] = f"ghcr.io/example/photo-prjct:{original_sha}"
    env["EXPECTED_REQUESTED_IMAGE"] = env["APP_IMAGE"]
    predecessor = tmp_path / ".deployment-previous.original"
    (predecessor / "deploy").mkdir(parents=True)
    for name in ("docker-compose.deployment.yml", "docker-compose.https.yml"):
        (predecessor / name).write_text("original predecessor\n")
    (predecessor / "deploy/evidence").write_text("pre-cutover source\n")
    env["PREVIOUS_DEPLOYMENT_PACKAGE_ROOT"] = str(predecessor)
    initial = _run("deploy/apply-deployment.sh", env=env)
    assert initial.returncode != 0
    assert (tmp_path / ".deployment-recovery/candidate.env").is_file()
    (tmp_path / "previous-env.expected").write_bytes((tmp_path / ".env").read_bytes())
    (tmp_path / "apply.log").write_text("")
    env.update(
        APPLY_SCENARIO="success",
        RECOVER_FORWARD="True",
        RELEASE_SHA=fix_sha,
        APP_IMAGE=f"ghcr.io/example/photo-prjct:{fix_sha}",
        EXPECTED_REQUESTED_IMAGE=f"ghcr.io/example/photo-prjct:{fix_sha}",
        EXPECTED_IMAGE_REVISION=fix_sha,
        SECRET_KEY="workflow-replacement-secret-must-not-win",
    )
    env.pop("PREVIOUS_DEPLOYMENT_PACKAGE_ROOT")
    return env, predecessor


def _run_forward_installer(tmp_path, fake_bin, env, *, candidate_deploy=None):
    candidate_deploy = candidate_deploy or tmp_path / "deploy"
    shutil.copy2(ROOT / "deploy/apply-deployment.sh", candidate_deploy / "apply-deployment.sh")
    archive_name = ".deployment-candidate.forward.tar"
    with tarfile.open(tmp_path / archive_name, "w") as archive:
        for name in ("docker-compose.deployment.yml", "docker-compose.https.yml", "deploy"):
            archive.add(candidate_deploy if name == "deploy" else tmp_path / name, arcname=name)
    source = (ROOT / "deploy/run-remote.sh").read_text()
    installer = source.split("deployment_command = r'''", 1)[1].split("'''\n", 1)[0]
    installer = installer.replace("deployment_root=/opt/photo-prjct", f"deployment_root={tmp_path}")
    _write_executable(fake_bin / "flock", 'printf "canonical-lock\\n" >> "$COMMAND_LOG"')
    return subprocess.run(
        ["sh", "-c", installer],
        env={**os.environ, **env, "DEPLOYMENT_ARCHIVE_NAME": archive_name},
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize("timing", ["migrate", "health"])
@pytest.mark.parametrize("fix_sha", ["a" * 40, "b" * 40], ids=["same-candidate", "forward-fix"])
def test_canonical_forward_recovery_consumes_retained_inputs_and_commits(
    tmp_path, fake_bin, timing, fix_sha
):
    env, predecessor = _trapped_forward_recovery(tmp_path, fake_bin, timing=timing, fix_sha=fix_sha)
    result = _run_forward_installer(tmp_path, fake_bin, env)
    assert result.returncode == 0, result.stderr
    assert "DEPLOY_RESULT=success phase=commit" in result.stdout
    assert (tmp_path / "deployed-image").read_text().strip() == env["APP_IMAGE"]
    assert "SECRET_KEY=new-secret\n" in (tmp_path / ".env").read_text()
    assert not (tmp_path / ".deployment-recovery").exists()
    assert not predecessor.exists()
    commands = _apply_log(tmp_path)
    assert commands.index("canonical-lock") < commands.index("forward-candidate-probe")
    assert "previous-web-processing-schema-probe" not in commands
    assert "private-forward-probe-detail" not in result.stdout + result.stderr
    assert "workflow-replacement-secret-must-not-win" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "scenario", ["forward-incompatible", "forward-unpaused", "wrong-revision", "health-failure"]
)
@pytest.mark.parametrize("timing", ["migrate", "health"])
def test_failed_forward_recovery_keeps_original_snapshot_and_never_rolls_back(
    tmp_path, fake_bin, scenario, timing
):
    env, predecessor = _trapped_forward_recovery(
        tmp_path, fake_bin, timing=timing, fix_sha="b" * 40
    )
    snapshot = tmp_path / ".deployment-recovery"
    before = {p.name: p.read_bytes() for p in snapshot.iterdir()}
    (tmp_path / "deploy/candidate-generation").write_text("last-compatible candidate\n")
    prospective = tmp_path / "prospective-deploy"
    shutil.copytree(tmp_path / "deploy", prospective)
    (prospective / "candidate-generation").write_text("forward-fix candidate\n")
    env["APPLY_SCENARIO"] = scenario
    if scenario == "wrong-revision":
        env["EXPECTED_IMAGE_REVISION"] = "c" * 40
    result = _run_forward_installer(tmp_path, fake_bin, env, candidate_deploy=prospective)
    assert result.returncode != 0
    assert {p.name: p.read_bytes() for p in snapshot.iterdir()} == before
    assert (predecessor / "deploy/evidence").read_text() == "pre-cutover source\n"
    commands = _apply_log(tmp_path)
    assert "previous-web-processing-schema-probe" not in commands
    if scenario == "wrong-revision":
        assert "does not match the approved SHA" in result.stderr
    else:
        assert "forward-candidate-probe" in commands
    assert not any("up -d --no-deps web nginx" in c and "APP_IMAGE=unset" in c for c in commands)
    if scenario == "health-failure":
        assert any(" stop web" in c for c in commands)
        assert (tmp_path / "deploy/candidate-generation").read_text() == "forward-fix candidate\n"
    else:
        assert not any(" up -d --no-deps web nginx" in c for c in commands)
        assert (
            tmp_path / "deploy/candidate-generation"
        ).read_text() == "last-compatible candidate\n"
    assert "private-forward-probe-detail" not in result.stdout + result.stderr
    assert "workflow-replacement-secret-must-not-win" not in result.stdout + result.stderr
    env["RECOVER_FORWARD"] = "False"
    ordinary = _run_forward_installer(tmp_path, fake_bin, env)
    assert ordinary.returncode != 0
    assert "recovery remains unfinished" in ordinary.stderr


@pytest.mark.parametrize("restore_fails", [False, True], ids=["restored", "restore-failed"])
def test_interrupted_forward_package_install_preserves_retryable_candidate(
    tmp_path, fake_bin, restore_fails
):
    env, predecessor = _trapped_forward_recovery(
        tmp_path, fake_bin, timing="health", fix_sha="b" * 40
    )
    snapshot = tmp_path / ".deployment-recovery"
    snapshot_before = {p.name: p.read_bytes() for p in snapshot.iterdir()}
    env_before = (tmp_path / ".env").read_bytes()
    image_before = (tmp_path / "deployed-image").read_bytes()
    https_before = (tmp_path / "docker-compose.https.yml").read_bytes()
    (tmp_path / "deploy/candidate-generation").write_text("last-compatible candidate\n")
    prospective = tmp_path / "prospective-deploy"
    shutil.copytree(tmp_path / "deploy", prospective)
    (prospective / "candidate-generation").write_text("forward-fix candidate\n")
    restore_failure = (
        "  */.deployment-previous.*/docker-compose.https.yml) exit 1 ;;\n" if restore_fails else ""
    )
    _write_executable(
        fake_bin / "mv",
        'case "$1" in\n'
        '  */.deployment-candidate.*/docker-compose.https.yml) kill -TERM "$PPID"; exit 143 ;;\n'
        + restore_failure
        + 'esac\nexec /bin/mv "$@"\n',
    )
    result = _run_forward_installer(tmp_path, fake_bin, env, candidate_deploy=prospective)
    assert result.returncode != 0
    assert {p.name: p.read_bytes() for p in snapshot.iterdir()} == snapshot_before
    assert (predecessor / "deploy/evidence").read_text() == "pre-cutover source\n"
    assert (tmp_path / ".env").read_bytes() == env_before
    assert (tmp_path / "deployed-image").read_bytes() == image_before
    assert (tmp_path / "deploy/candidate-generation").read_text() == "last-compatible candidate\n"
    assert "previous-web-processing-schema-probe" not in _apply_log(tmp_path)
    assert "workflow-replacement-secret-must-not-win" not in result.stdout + result.stderr
    if restore_fails:
        retained = [p for p in tmp_path.glob(".deployment-previous.*") if p != predecessor]
        assert len(retained) == 1
        assert (retained[0] / "docker-compose.https.yml").read_bytes() == https_before
        staging = list(tmp_path.glob(".deployment-candidate.*/"))
        assert len(staging) == 1
        assert (staging[0] / "docker-compose.https.yml").is_file()
        assert (staging[0] / "deploy/candidate-generation").read_text() == "forward-fix candidate\n"
        return
    assert result.returncode == 143
    assert (tmp_path / "docker-compose.deployment.yml").is_file()
    assert (tmp_path / "docker-compose.https.yml").read_bytes() == https_before
    assert list(tmp_path.glob(".deployment-previous.*")) == [predecessor]
    assert not list(tmp_path.glob(".deployment-candidate.*/"))
    (fake_bin / "mv").unlink()
    retried = _run_forward_installer(tmp_path, fake_bin, env, candidate_deploy=prospective)
    assert retried.returncode == 0, retried.stderr
    assert "DEPLOY_RESULT=success phase=commit" in retried.stdout
    assert (tmp_path / "deploy/candidate-generation").read_text() == "forward-fix candidate\n"
    assert not snapshot.exists()
    assert not predecessor.exists()


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
    commands = _apply_log(tmp_path)
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert not any(" up -d --no-deps web nginx" in command for command in commands)
    assert not any(command.startswith("crontab ") for command in commands)
    assert commands.count("candidate-requested-env-with-canonical-untouched") == 1
    _assert_no_env_temporary_files(tmp_path)


def test_workflow_forwards_private_media_settings_through_the_deploy_consumer() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "PRIVATE_MEDIA_S3_BUCKET: ${{ vars.PRIVATE_MEDIA_S3_BUCKET }}" in workflow
    assert "PRIVATE_MEDIA_S3_ACCESS_KEY_ID" not in workflow
    assert "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY" not in workflow
    assert "--consumer deploy" in workflow


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


def test_deployment_apply_activates_https_edge_and_public_checks(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="success"),
    )

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert "docker-compose.https.yml" in commands
    assert "docker-compose.deployment.yml" in commands
    assert "stop nginx" in commands
    assert "reconcile-certificate" in commands
    assert "https://findme-photo.ru/health/" in commands
    assert "verify-public-edge" in commands


def test_deployment_apply_does_not_write_a_runtime_environment_label(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario="success"),
    )

    assert result.returncode == 0, result.stderr
    assert "MONITORING_ENVIRONMENT=" not in (tmp_path / ".env").read_text(encoding="utf-8")


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
    assert commands.count("up -d --no-deps web nginx") == 1
    assert commands.count("requested-env-promoted-before-stop") == 1
    assert "https://findme-photo.ru/health/" in commands
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize("scenario", ["health-failure", "public-failure"])
def test_post_activation_failure_retains_candidate_without_committing_image_marker(
    tmp_path: Path, fake_bin: Path, scenario: str
) -> None:
    result = _run(
        "deploy/apply-deployment.sh",
        env=_apply_env(tmp_path, fake_bin, scenario=scenario),
    )

    assert result.returncode != 0
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / ".env").read_bytes() != PREVIOUS_ENV
    assert (tmp_path / ".deployment-recovery/candidate.env").read_bytes() == (
        tmp_path / ".env"
    ).read_bytes()
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.count("up -d --no-deps web nginx") == 1
    assert "previous-web-processing-schema-probe" not in commands


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
    assert commands.index("stop nginx") < commands.index("up -d --no-deps web nginx")
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
    commands = _apply_log(tmp_path)
    assert commands.count("candidate-requested-env-with-canonical-untouched") == 11
    assert not any(" stop nginx" in command for command in commands)
    assert "reconcile-certificate" not in commands
    assert sum(" up -d --no-deps web nginx" in command for command in commands) == 1
    assert any(
        "APP_IMAGE=unset" in command and " up -d --no-deps web nginx" in command
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
    commands = _apply_log(tmp_path)
    assert sum(" up -d --no-deps web nginx" in command for command in commands) == 1
    assert requested_secret not in result.stdout
    assert requested_secret not in result.stderr
    assert requested_secret not in "\n".join(commands)
    _assert_no_env_temporary_files(tmp_path)


@pytest.mark.parametrize(
    ("scenario", "expected_reconciliations"),
    [("marker-failure", 1)],
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
    assert (tmp_path / ".env").read_bytes() != PREVIOUS_ENV
    assert (tmp_path / ".deployment-recovery/candidate.env").read_bytes() == (
        tmp_path / ".env"
    ).read_bytes()
    commands = (tmp_path / "apply.log").read_text(encoding="utf-8")
    assert commands.count("up -d --no-deps web nginx") == expected_reconciliations
    assert "previous-web-processing-schema-probe" not in commands


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
    compose = (ROOT / "docker-compose.deployment.yml").read_text(encoding="utf-8")

    assert "SELFIE_FEEDBACK_ENABLED: ${{ vars.SELFIE_FEEDBACK_ENABLED || 'False' }}" in workflow
    assert "SELFIE_FEEDBACK_S3_BUCKET: ${{ vars.SELFIE_FEEDBACK_S3_BUCKET }}" in workflow
    assert "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID" not in workflow
    assert "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY" not in workflow
    assert "SELFIE_FEEDBACK_KMS_KEY_ID: ${{ vars.SELFIE_FEEDBACK_KMS_KEY_ID }}" in workflow
    assert "--consumer deploy" in workflow
    assert "selfie-feedback-storage" in workflow
    assert "worker-bulk:" not in compose
    assert "SELFIE_FEEDBACK_" not in (ROOT / "deploy/worker-pools/compose.yml").read_text()


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
    assert "CONTAINER_TAG=findme.service=web" in journal_calls
    assert "findme.environment=" not in journal_calls


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
    assert (tmp_path / ".env").read_bytes() != PREVIOUS_ENV
    assert "previous-web-processing-schema-probe" not in commands
    assert any(" stop web" in command for command in commands)
    sudo_commands = [command for command in commands if command.startswith("sudo ")]
    assert sudo_commands
    assert all("sudo -n " in command and " -E " not in f" {command} " for command in sudo_commands)
    assert all(
        (
            "/usr/local/sbin/findme-selfie-observability" in command
            or "/usr/local/sbin/findme-worker-pool-metrics" in command
        )
        for command in sudo_commands
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
    (tmp_path / ".env").write_text(
        "APP_IMAGE=test\nPHOTO_PROCESSING_ENABLED=True\n", encoding="utf-8"
    )
    (tmp_path / "docker-compose.deployment.yml").write_text("services: {}\n", encoding="utf-8")
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
  *" ps -q worker-bulk") printf 'worker-bulk-id\n' ;;
  *" ps -q worker-selfie") printf 'worker-selfie-id\n' ;;
  *"inspect "*web-id*)
    [ "$VERIFY_SCENARIO" = wrong-tag ] && printf 'json-file|wrong\n' || \
      printf 'journald|findme.service=web\n'
    ;;
  *"inspect "*nginx-id*)
    printf 'journald|findme.service=nginx\n'
    ;;
  *"inspect "*worker-bulk-id*)
    printf 'journald|findme.service=worker-bulk\n'
    ;;
  *"inspect "*worker-selfie-id*)
    printf 'journald|findme.service=worker-selfie\n'
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
        "COMPOSE_PROJECT_NAME": "photo-prjct",
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


@pytest.mark.parametrize("scenario", ["vector-capability-failure", "vector-database-start-failure"])
def test_vector_capability_failure_rolls_back_before_candidate_migration(
    tmp_path, fake_bin, scenario
):
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    result = _run("deploy/apply-deployment.sh", env=env)
    commands = Path(env["COMMAND_LOG"]).read_text()
    assert result.returncode != 0
    assert (
        "DEPLOY_RESULT=failure phase=vector-database-preflight rollback=succeeded" in result.stdout
    )
    assert "candidate-migrate" not in commands


def test_vector_database_is_reconciled_and_verified_before_candidate_migration(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="success")
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    commands = Path(env["COMMAND_LOG"]).read_text()
    assert (
        commands.index("candidate-vector-database-start")
        < commands.index("candidate-vector-collation-check")
        < commands.index("candidate-vector-capability")
        < commands.index("candidate-migrate")
    )


def test_database_collation_mismatch_refuses_extension_and_retains_previous_database_image(
    tmp_path, fake_bin
):
    env = _apply_env(tmp_path, fake_bin, scenario="vector-collation-mismatch")
    (tmp_path / "docker-compose.deployment.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n"
    )
    result = _run("deploy/apply-deployment.sh", env=env)
    commands = Path(env["COMMAND_LOG"]).read_text()
    assert result.returncode != 0
    assert (
        "DEPLOY_RESULT=failure phase=vector-database-preflight rollback=succeeded" in result.stdout
    )
    assert "candidate-vector-collation-check" in commands
    assert "candidate-vector-capability" not in commands
    assert "candidate-migrate" not in commands
    assert "image: postgres:16" in (tmp_path / "docker-compose.deployment.yml").read_text()
    assert "pgvector/pgvector" not in (tmp_path / "docker-compose.deployment.yml").read_text()


def test_native_only_health_failure_preserves_candidate_for_forward_recovery(
    tmp_path: Path, fake_bin: Path
) -> None:
    result = _run(
        "deploy/apply-deployment.sh", env=_apply_env(tmp_path, fake_bin, scenario="health-failure")
    )
    assert result.returncode != 0
    assert "DEPLOY_RESULT=failure phase=local-health rollback=failed" in result.stdout
    commands = _apply_log(tmp_path)
    assert any("up -d --no-deps web nginx" in command for command in commands)
    assert any(" stop web" in command for command in commands)
    assert "previous-web-processing-schema-probe" not in commands
    assert not any(
        "up -d --no-deps web nginx" in command and "APP_IMAGE=unset" in command
        for command in commands
    )
    recovery = tmp_path / ".deployment-recovery"
    assert (recovery / "previous.env").read_bytes() == PREVIOUS_ENV
    assert (recovery / "candidate.env").read_bytes() == (tmp_path / ".env").read_bytes()
    assert (tmp_path / "deployed-image").read_text().strip() == "old-image"
