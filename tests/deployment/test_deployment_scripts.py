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
    # Docker process snapshots are the external reload acknowledgement in these
    # focused cases. Live generation and drain behavior is exercised separately.
    if env and "PATH" in env:
        docker = Path(env["PATH"].split(":")[0]) / "docker"
        if docker.is_file() and "ps -o pid,args" not in docker.read_text():
            docker.write_text(
                docker.read_text().replace(
                    "set -eu\n",
                    'set -eu\ncase "$*" in *"ps -o pid,args") '
                    'count=10; [ ! -f "$0.count" ] || count=$(cat "$0.count"); '
                    'count=$((count + 1)); echo "$count" > "$0.count"; '
                    'printf "%s nginx: worker process\\n" "$count"; exit 0 ;; esac\n',
                    1,
                )
            )
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
  *"cat /etc/nginx/conf.d/default.conf")
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
  *"cat /etc/nginx/conf.d/default.conf")
    printf 'upstream django_upstream { server web:8000; }\\n' ;;
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
  *"cat /etc/nginx/conf.d/default.conf")
    slot=web-next; [ ! -f "$0.switched" ] || slot=web
    printf 'upstream django_upstream { server %s:8000; }\\n' "$slot" ;;
  *"--apply --slot web") touch "$0.switched" ;;
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
  *"cat /etc/nginx/conf.d/default.conf")
    slot=web-next; [ ! -f "$0.switched" ] || slot=web
    printf 'upstream django_upstream { server %s:8000; }\\n' "$slot" ;;
  *"ps -q web") printf 'old-id\\n' ;;
  "inspect "*) printf '%s\\n' "$HEALTH" ;;
  *"--apply --slot web") touch "$0.switched" ;;
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


@pytest.mark.parametrize("draining", [False, True])
def test_web_slot_drain_never_stops_a_backend_with_accepted_requests(tmp_path, fake_bin, draining):
    _write_executable(
        fake_bin / "docker",
        """
case "$*" in
  *"ps -o pid,args")
    printf 'PID COMMAND\\n10 nginx: worker process\\n'
    [ "$DRAINING" = no ] || printf '9 nginx: worker process is shutting down\\n'
    ;;
  *) exit 2 ;;
esac
""",
    )
    result = _slot_command(
        tmp_path,
        "drain",
        "--timeout",
        "0",
        env={"PATH": f"{fake_bin}:{os.environ['PATH']}", "DRAINING": "yes" if draining else "no"},
    )
    assert result.returncode == (1 if draining else 0), result.stderr
    if draining:
        assert "accepted requests" in result.stderr


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
if arguments[-4:] == ['ps', '-a', '-q', 'web']:
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
        ("upstream django_upstream { server web:8000; }\n" * 2, "healthy", 1),
        ("upstream django_upstream { server web:8000; } " * 2, "healthy", 1),
        ("# upstream django_upstream { server web:8000; }", "healthy", 1),
        (
            "upstream django_upstream { server web:8000; server web-next:8000; }",
            "healthy",
            1,
        ),
    ],
)
def test_missing_slot_selection_requires_live_single_slot_proof(
    tmp_path, fake_bin, configuration, health, expected
):
    _write_executable(
        fake_bin / "docker",
        """
case "$*" in
  *"cat /etc/nginx/conf.d/default.conf") printf '%s\\n' "$CONFIGURATION" ;;
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
    (deployment_root / "deploy").mkdir(parents=True)
    (deployment_root / "deploy/web-slot.py").write_text("slot selector fixture\n")
    _write_executable(fake_bin / "python3", 'printf "web\\n"')
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
    _write_executable(fake_bin / "python3", 'printf "web\\n"')
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


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("deploy/run-commerce-worker-health.sh", "commerce_worker_health"),
        ("deploy/run-upload-cleanup.sh", "cleanup_stale_uploads"),
        ("deploy/run-cart-cleanup.sh", "cleanup_expired_carts"),
    ],
)
def test_scheduled_django_commands_use_selected_slot_when_web_is_stopped(
    tmp_path: Path, fake_bin: Path, script: str, expected: str
) -> None:
    deployment = tmp_path / "deployment"
    (deployment / "deploy").mkdir(parents=True)
    (deployment / ".env").write_text("APP_IMAGE=test\n", encoding="utf-8")
    (deployment / "deploy/web-slot.py").write_text("selected-slot CLI fixture\n")
    (deployment / "deploy/nginx").mkdir()
    (deployment / "deploy/nginx/selected-slot").write_text("web-next\n")
    _write_executable(
        fake_bin / "python3",
        'case "$*" in *web-slot.py*) printf "%s\\n" "$SELECTED_SLOT" ;; *) exit 2 ;; esac',
    )
    _write_executable(
        fake_bin / "docker",
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'case " $* " in *" exec -T web-next "*) exit 0 ;; *) exit 3 ;; esac',
    )
    _write_executable(fake_bin / "flock", 'shift 4\nexec "$@"')
    command_log = tmp_path / "commands.log"
    result = _run(
        script,
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(deployment),
            "SELECTED_SLOT": "web-next",
            "COMMAND_LOG": str(command_log),
        },
    )

    assert result.returncode == 0, result.stderr
    command = command_log.read_text(encoding="utf-8")
    assert "exec -T web-next" in command
    assert expected in command
    assert "exec -T web " not in command


def test_scheduled_django_command_fails_when_slot_selection_is_unavailable(
    tmp_path: Path, fake_bin: Path
) -> None:
    deployment = tmp_path / "deployment"
    (deployment / "deploy").mkdir(parents=True)
    _write_executable(fake_bin / "python3", "exit 1")
    _write_executable(fake_bin / "docker", 'printf called > "$COMMAND_LOG"')
    _write_executable(fake_bin / "flock", 'shift 4\nexec "$@"')
    command_log = tmp_path / "commands.log"
    result = _run(
        "deploy/run-cart-cleanup.sh",
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(deployment),
            "COMMAND_LOG": str(command_log),
        },
    )

    assert result.returncode != 0
    assert not command_log.exists()


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
    (deploy_dir / "nginx").mkdir()
    (deploy_dir / "nginx/selected-slot").write_text("web\n")
    _write_python_executable(
        deploy_dir / "web-slot.py",
        """
import os, pathlib, sys
root = pathlib.Path(sys.argv[2])
command, *arguments = sys.argv[3:]
marker = root / 'deploy/nginx/selected-slot'
scenario = os.environ['APPLY_SCENARIO']
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write('slot ' + ' '.join([command, *arguments]) + '\\n')
if command in ('selected', 'installed', 'reconcile'):
    print(marker.read_text().strip())
elif command == 'switch':
    if scenario == 'switch-failure' and arguments == ['web-next']:
        sys.exit(1)
    marker.write_text(arguments[0] + '\\n')
elif command == 'drain':
    if scenario == 'drain-timeout' and marker.read_text().strip() == 'web-next':
        sys.exit(1)
""",
    )
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
if [ "$APPLY_SCENARIO" = public-failure ]; then
  [ "$(cat "$DEPLOY_ROOT/deploy/nginx/selected-slot")" = web ] || exit 1
fi
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
if [ "${1-}" = image ] && [ "${2-}" = inspect ] && [ "${4-}" = '{{.Id}}' ]; then
  printf '%s\n' "${PULLED_IMAGE_ID:-${EXPECTED_REQUESTED_IMAGE:-new-image}}"
  exit 0
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
docker_arguments="$*"
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
  expected_image="${EXPECTED_REQUESTED_IMAGE:-new-image}"
  case " $docker_arguments " in *" pull web "*) expected_image="${APP_IMAGE:-new-image}" ;; esac
  [ "$(sed -n 's/^APP_IMAGE=//p' "$compose_env_file")" = "$expected_image" ]
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
  *" up -d --wait --wait-timeout 120 --no-deps "*)
    case "$APPLY_SCENARIO" in compose-failure|recovery-failure) exit 1 ;; esac
    ;;
  *" ps -q web-next "*) printf 'web-id\\n'; exit 0 ;;
  *" ps -a -q web "*|*" ps -a -q web-next "*) printf 'web-id\\n'; exit 0 ;;
  *" exec -T web-next python manage.py smoke_gallery_media_projection "*)
    printf 'candidate-gallery-projection-smoke\\n' >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != gallery-projection-smoke-failure ] || exit 1
    ;;
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
  *" compose "*" ps -q import-worker "*) printf 'import-worker-id\n' ;;
  *" inspect "*" web-id "*) sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env" ;;
  *" inspect "*" commerce-worker-id "*) printf 'true false false\n' ;;
  *" inspect "*" import-worker-id "*) printf 'true false false\n' ;;
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
  *" compose "*" commerce_worker_health "*)
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
  [ "$(cat "$DEPLOY_ROOT/deploy/nginx/selected-slot")" = web ]
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
        "DEPLOY_WEB": "true",
        "DEPLOY_IMPORT": "true",
        "DEPLOY_COMMERCE": "true",
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
    "edge-switch",
    "local-health",
    "gallery-media-smoke",
    "worker-health",
    "public-health",
    "predecessor-drain",
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


ENTRYPOINT_ENV = {
    "SECRET_KEY": "local-test-key",
    "DEBUG": "False",
    "ALLOWED_HOSTS": "192.0.2.44,web,localhost",
    "DB_NAME": "app",
    "DB_USER": "app",
    "DB_PASSWORD": "local-test-only",
    "PUBLIC_DOMAIN": "findme-photo.ru",
    "GALLERY_CDN_ORIGIN": "http://192.0.2.44",
    "GALLERY_CDN_TOKEN_SECRET": "local-test-token",
    "GALLERY_IMGPROXY_KEY": "local-test-imgproxy-key",
    "GALLERY_IMGPROXY_SALT": "local-test-imgproxy-salt",
    "MEDIA_STORAGE_BACKEND": "s3",
    "PHOTO_PROCESSING_ENABLED": "True",
    "PHOTO_PROCESSING_FACE_ENABLED": "True",
    "MEDIA_S3_PUBLIC_BUCKET": "public-test-bucket",
    "MEDIA_S3_ACCESS_KEY_ID": "public-test-key",
    "MEDIA_S3_SECRET_ACCESS_KEY": "public-test-secret",
    "PRIVATE_MEDIA_S3_BUCKET": "private-test-bucket",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "private-test-key",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "private-test-secret",
}


def test_entrypoint_runs_gunicorn_with_the_stable_profile(tmp_path: Path, fake_bin: Path) -> None:
    """The running web process must keep workers warm without request-count recycling."""
    _write_executable(fake_bin / "python", "exit 0")
    _write_executable(fake_bin / "gunicorn", 'printf "%s\\n" "$*" > "$GUNICORN_LOG"')

    result = subprocess.run(
        ["sh", ROOT / "src/backend/entrypoint.sh"],
        env={
            **os.environ,
            **ENTRYPOINT_ENV,
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
            **ENTRYPOINT_ENV,
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
    assert (tmp_path / "commands.log").read_text(encoding="utf-8").splitlines()[0] == (
        "python manage.py collectstatic --noinput"
    )
    assert (
        (tmp_path / "commands.log")
        .read_text(encoding="utf-8")
        .splitlines()[1]
        .startswith("gunicorn ")
    )


def test_entrypoint_recreates_the_shared_multiprocess_directory_before_gunicorn(
    tmp_path: Path, fake_bin: Path
) -> None:
    _write_executable(fake_bin / "python", 'printf "python %s\\n" "$*" >> "$COMMAND_LOG"')
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
            **ENTRYPOINT_ENV,
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
        "python manage.py collectstatic --noinput",
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
            **ENTRYPOINT_ENV,
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


def _projection_cutover_env(tmp_path: Path, fake_bin: Path, *, scenario: str) -> dict[str, str]:
    env = _apply_env(tmp_path, fake_bin, scenario=scenario)
    previous_env = PREVIOUS_ENV + (
        b"PHOTO_PROCESSING_ENABLED=True\nPHOTO_WORKER_REPLICAS=1\nCOMMERCE_WORKER_ENABLED=False\n"
    )
    (tmp_path / ".env").write_bytes(previous_env)
    (tmp_path / "previous-env.expected").write_bytes(previous_env)
    return env


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


def _trapped_forward_recovery(tmp_path, fake_bin, *, timing, fix_sha, worker=None):
    original_sha = "a" * 40
    env = _apply_env(tmp_path, fake_bin, scenario=f"processing-schema-{timing}-failure")
    env["APP_IMAGE"] = f"ghcr.io/example/photo-prjct:{original_sha}"
    env["EXPECTED_REQUESTED_IMAGE"] = env["APP_IMAGE"]
    if worker:
        env.update(DEPLOY_IMPORT="false", DEPLOY_COMMERCE="false")
        if worker == "commerce":
            env.update(_real_commerce_worker_settings())
            env.update(DEPLOY_COMMERCE="true", APPLY_SCENARIO="commerce-worker-health-failure")
        else:
            env.update(
                DEPLOY_IMPORT="true",
                PHOTO_IMPORT_ENABLED="True",
                PHOTO_IMPORT_WORKER_TOKEN="retained-import-token",
                PHOTO_IMPORT_BUILD="retained-import-build",
                IMPORT_WORKER_IMAGE="ghcr.io/example/photo-prjct-import-worker:latest",
                APPLY_SCENARIO="import-start-failure",
            )
        _install_forward_import_probe(fake_bin)
    predecessor = tmp_path / ".deployment-previous.original"
    (predecessor / "deploy").mkdir(parents=True)
    for name in ("docker-compose.deployment.yml", "docker-compose.https.yml"):
        (predecessor / name).write_text("original predecessor\n")
    (predecessor / "deploy/evidence").write_text("pre-cutover source\n")
    env["PREVIOUS_DEPLOYMENT_PACKAGE_ROOT"] = str(predecessor)
    initial = _run("deploy/apply-deployment.sh", env=env)
    assert initial.returncode != 0
    if worker:
        assert "DEPLOY_RESULT=failure phase=worker-health" in initial.stdout
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


def _install_forward_import_probe(fake_bin):
    docker = fake_bin / "docker"
    docker.write_text(
        docker.read_text().replace(
            "set -eu",
            """set -eu
case " $* " in
  *" image inspect "*"photo-prjct-import-worker"*|*" image inspect "*"retained-import-id"*)
    echo retained-import-id; exit 0 ;;
  *" compose "*" up -d --no-deps import-worker "*)
    echo import-start >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != import-start-failure ]; exit ;;
  *" compose "*" ps -q import-worker "*) echo import-id; exit 0 ;;
  *" inspect "*" import-id "*)
    if [ "$APPLY_SCENARIO" = import-not-running ]; then echo 'false false false'
    else echo 'true false false'; fi
    exit 0 ;;
  *" compose "*" exec -T import-worker python -m import_worker --check-ready "*)
    echo import-running-readiness >> "$COMMAND_LOG"
    [ "$APPLY_SCENARIO" != import-not-ready ]; exit ;;
esac
""",
            1,
        )
    )


def test_workflow_forwards_private_media_settings_through_the_deploy_consumer() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "PRIVATE_MEDIA_S3_BUCKET: ${{ vars.PRIVATE_MEDIA_S3_BUCKET }}" in workflow
    assert "PRIVATE_MEDIA_S3_ACCESS_KEY_ID" not in workflow
    assert "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY" not in workflow
    assert "--consumer deploy" in workflow


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
    selector_marker = tmp_path / "deployment-selector-executed"
    (tmp_path / "deploy/web-slot.py").write_text(
        f"from pathlib import Path\nPath({str(selector_marker)!r}).touch()\nprint('web')\n",
        encoding="utf-8",
    )

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
    assert not selector_marker.exists()


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
            "web-next",
        ],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert (result.returncode == 0) is expected_success, result.stderr
    journal_calls = (tmp_path / "systemctl.log.probe-journal").read_text(encoding="utf-8")
    assert "CONTAINER_TAG=findme.service=web-next" in journal_calls
    assert "findme.environment=" not in journal_calls


@pytest.mark.parametrize(
    "arguments",
    [
        ["verify-probe", "not-a-uuid"],
        ["verify-probe", "00000000-0000-0000-0000-000000000001", "extra"],
        ["verify-probe", "00000000-0000-0000-0000-000000000001", "web-old"],
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
        ("selected-next", True),
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
  *" ps -q web")
    [ "$VERIFY_SCENARIO" != selected-next ] && printf 'web-id\n' ;;
  *" ps -q web-next")
    [ "$VERIFY_SCENARIO" = selected-next ] && printf 'web-next-id\n' ;;
  *" ps -q nginx") printf 'nginx-id\n' ;;
  *" ps -q worker-bulk") printf 'worker-bulk-id\n' ;;
  *" ps -q worker-selfie") printf 'worker-selfie-id\n' ;;
  *"inspect "*web-id*)
    [ "$VERIFY_SCENARIO" = wrong-tag ] && printf 'json-file|wrong\n' || \
      printf 'journald|findme.service=web\n'
    ;;
  *"inspect "*web-next-id*) printf 'journald|findme.service=web-next\n' ;;
  *"inspect "*nginx-id*)
    printf 'journald|findme.service=nginx\n'
    ;;
  *"inspect "*worker-bulk-id*)
    printf 'journald|findme.service=worker-bulk\n'
    ;;
  *"inspect "*worker-selfie-id*)
    printf 'journald|findme.service=worker-selfie\n'
    ;;
  *" exec -T "$SELECTED_SLOT" "*) printf '%s\n' "$*" > "$PROBE_COMMAND_LOG" ;;
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
    # Deterministic selection and UUID keep this host harness independent of Docker state.
    _write_executable(
        fake_bin / "python3",
        'case "$*" in *web-slot.py*) printf "%s\\n" "$SELECTED_SLOT" ;; '
        '*) printf "00000000-0000-0000-0000-000000000001\\n" ;; esac',
    )
    _write_executable(
        fake_bin / "sudo",
        """
[ "${1-}" = -n ] && shift
[ "${1-}" = /usr/local/sbin/findme-selfie-observability ] || exit 2
[ "${2-}" = verify-probe ] || exit 2
[ "${3-}" = 00000000-0000-0000-0000-000000000001 ] || exit 2
[ "${4-}" = "$SELECTED_SLOT" ] || exit 2
[ "$VERIFY_SCENARIO" != unreadable-probe ]
""",
    )
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOY_ROOT": str(tmp_path),
        "COMPOSE_PROJECT_NAME": "photo-prjct",
        "SELFIE_OBSERVABILITY_JOURNAL_DIR": str(tmp_path / "journal"),
        "VERIFY_SCENARIO": scenario,
        "SELECTED_SLOT": "web-next" if scenario == "selected-next" else "web",
        "EXPECTED_PROBE_LINE": '"probe_id":"00000000-0000-0000-0000-000000000001"',
        "PROBE_COMMAND_LOG": str(tmp_path / "probe-command.log"),
    }

    result = _run("deploy/verify-selfie-observability.sh", env=env)

    assert (result.returncode == 0) is expected_success, result.stderr
    probe_command_log = tmp_path / "probe-command.log"
    if scenario != "wrong-tag":
        probe_command = probe_command_log.read_text(encoding="utf-8")
        selected = "web-next" if scenario == "selected-next" else "web"
        assert f" exec -T {selected} sh -c " in probe_command
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
