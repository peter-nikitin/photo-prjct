import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run_script(
    script: str,
    *args: str,
    env: dict[str, str],
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", ROOT / script, *args],
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    return directory


def _certificate_env(tmp_path: Path, fake_bin: Path, sans: str) -> dict[str, str]:
    log = tmp_path / "docker.log"
    _write_executable(
        fake_bin / "docker",
        """
printf '%s\n' "$*" >> "$COMMAND_LOG"
case " $* " in
  *" --entrypoint openssl "*)
    [ "$CERTIFICATE_STATE" != missing ] || exit 1
    printf 'X509v3 Subject Alternative Name:\n    %s\n' "$CERTIFICATE_STATE"
    ;;
esac
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "CERTIFICATE_STATE": sans,
        "COMPOSE_PROJECT_NAME": "photo-test",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": "www.findme-photo.ru",
        "LETSENCRYPT_EMAIL": "ops@example.com",
    }


def test_matching_certificate_sans_skip_issuance(tmp_path: Path, fake_bin: Path) -> None:
    env = _certificate_env(
        tmp_path,
        fake_bin,
        "DNS:www.findme-photo.ru, DNS:findme-photo.ru",
    )

    result = _run_script("deploy/certbot/reconcile-certificate.sh", env=env)

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "docker.log").read_text(encoding="utf-8").splitlines()
    inspections = [command for command in commands if "--entrypoint openssl" in command]
    assert len(inspections) == 1
    assert all("certonly" not in command for command in commands)


@pytest.mark.parametrize(
    ("state", "force_renewal"),
    [("DNS:findme-photo.ru", True), ("missing", False)],
)
def test_certificate_state_is_issued_once_with_exact_names(
    tmp_path: Path,
    fake_bin: Path,
    state: str,
    force_renewal: bool,
) -> None:
    env = _certificate_env(tmp_path, fake_bin, state)

    result = _run_script("deploy/certbot/reconcile-certificate.sh", env=env)

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "docker.log").read_text(encoding="utf-8").splitlines()
    issuance = [command for command in commands if " certonly " in f" {command} "]
    assert len(issuance) == 1
    command = issuance[0]
    assert "--network host" in command
    assert "certbot/certbot:v2.11.0 certonly --standalone" in command
    assert "--cert-name photo-prjct" in command
    assert "--non-interactive --agree-tos --email ops@example.com" in command
    assert command.count(" -d ") == 2
    assert "-d findme-photo.ru" in command
    assert "-d www.findme-photo.ru" in command
    assert ("--force-renewal" in command) is force_renewal


def test_finalize_rejects_mismatched_candidate_without_marker_changes(tmp_path: Path) -> None:
    (tmp_path / "candidate-image").write_text("new-image\n", encoding="utf-8")
    (tmp_path / "deployed-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / "previous-image").write_text("older-image\n", encoding="utf-8")

    result = _run_script(
        "deploy/finalize-deployment.sh",
        "different-image",
        env={"DEPLOY_ROOT": str(tmp_path)},
    )

    assert result.returncode != 0
    assert (tmp_path / "candidate-image").read_text(encoding="utf-8") == "new-image\n"
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert (tmp_path / "previous-image").read_text(encoding="utf-8") == "older-image\n"


def test_finalize_atomically_promotes_matching_candidate(tmp_path: Path) -> None:
    (tmp_path / "candidate-image").write_text("new-image\n", encoding="utf-8")
    (tmp_path / "deployed-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / "previous-image").write_text("old-image\n", encoding="utf-8")

    result = _run_script(
        "deploy/finalize-deployment.sh",
        "new-image",
        env={"DEPLOY_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "new-image\n"
    assert not (tmp_path / "candidate-image").exists()
    assert not (tmp_path / "previous-image").exists()


def _rollback_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "docker",
        """
printf 'APP_IMAGE=%s docker %s\n' "${APP_IMAGE-unset}" "$*" >> "$COMMAND_LOG"
case " $* " in
  *" compose "*" ps -q web "*) printf 'web-id\n' ;;
  *" inspect "*" web-id "*) printf '%s\n' "$PREVIOUS_IMAGE" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "curl",
        'printf \'curl %s\n\' "$*" >> "$COMMAND_LOG"',
    )
    _write_executable(fake_bin / "sleep", ":")
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "DEPLOY_ROOT": str(tmp_path),
        "COMPOSE_PROJECT_NAME": "photo-test",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PREVIOUS_IMAGE": "old-image",
        "APP_IMAGE": "new-image",
    }


@pytest.mark.parametrize("missing", ["candidate", "previous"])
def test_rollback_refuses_incomplete_marker_state(
    tmp_path: Path,
    fake_bin: Path,
    missing: str,
) -> None:
    if missing != "candidate":
        (tmp_path / "candidate-image").write_text("new-image\n", encoding="utf-8")
    if missing != "previous":
        (tmp_path / "previous-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / "deployed-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / ".env").write_text("APP_IMAGE=new-image\nSECRET_KEY=keep\n", encoding="utf-8")
    env = _rollback_env(tmp_path, fake_bin)

    result = _run_script(
        "deploy/rollback-deployment.sh",
        "new-image",
        "http",
        env=env,
    )

    assert result.returncode != 0
    assert not (tmp_path / "commands.log").exists()
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"


@pytest.mark.parametrize(
    ("mode", "overlay", "health_url", "health_port"),
    [
        ("http", "docker-compose.staging.yml", "http://findme-photo.ru/health/", ":80:"),
        ("https", "docker-compose.https.yml", "https://findme-photo.ru/health/", ":443:"),
    ],
)
def test_successful_rollback_restores_previous_image_and_requested_edge(
    tmp_path: Path,
    fake_bin: Path,
    mode: str,
    overlay: str,
    health_url: str,
    health_port: str,
) -> None:
    (tmp_path / "candidate-image").write_text("new-image\n", encoding="utf-8")
    (tmp_path / "previous-image").write_text("old-image\n", encoding="utf-8")
    (tmp_path / "deployed-image").write_text("newer-stable-image\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "APP_IMAGE=new-image\nSECRET_KEY=keep\nDB_PASSWORD=unchanged\n",
        encoding="utf-8",
    )
    env = _rollback_env(tmp_path, fake_bin)

    result = _run_script(
        "deploy/rollback-deployment.sh",
        "new-image",
        mode,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "docker-compose.prod.yml" in commands
    assert overlay in commands
    assert "compose" in commands and "up -d --remove-orphans" in commands
    assert "APP_IMAGE=unset docker" in commands
    assert "down" not in commands
    assert "volume rm" not in commands
    assert health_url in commands
    assert health_port in commands
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "APP_IMAGE=old-image\nSECRET_KEY=keep\nDB_PASSWORD=unchanged\n"
    )
    assert (tmp_path / "deployed-image").read_text(encoding="utf-8") == "old-image\n"
    assert not (tmp_path / "candidate-image").exists()
    assert not (tmp_path / "previous-image").exists()


def _public_verify_env(
    tmp_path: Path,
    fake_bin: Path,
    *,
    alias: str = "",
    a_record: str = "111.88.151.64",
    aaaa_record: str = "",
    redirect_code: str = "308",
    redirect_url: str = "https://findme-photo.ru/__edge_verify__?source=github",
    health_code: str = "200",
    sans: str = "DNS:findme-photo.ru",
) -> dict[str, str]:
    log = tmp_path / "public.log"
    _write_executable(
        fake_bin / "curl",
        """
printf 'curl %s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *"dns.google/resolve"*"type=AAAA"*)
    if [ -n "$AAAA_RECORD" ]; then
      printf '{"Status":0,"Answer":[{"type":28,"data":"%s"}]}\n' "$AAAA_RECORD"
    else
      printf '{"Status":0,"Answer":[]}\n'
    fi
    ;;
  *"dns.google/resolve"*"type=A"*)
    printf '{"Status":0,"Answer":[{"type":1,"data":"%s"}]}\n' "$A_RECORD"
    ;;
  *"http://"*) printf '%s\n%s\n' "$REDIRECT_CODE" "$REDIRECT_URL" ;;
  *"/health/"*) printf '%s\n' "$HEALTH_CODE" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "openssl",
        """
printf 'openssl %s\n' "$*" >> "$COMMAND_LOG"
case "$1" in
  s_client) printf 'fake certificate\n' ;;
  x509) printf 'X509v3 Subject Alternative Name:\n    %s\n' "$SERVED_SANS" ;;
esac
""",
    )
    _write_executable(fake_bin / "timeout", 'shift\nexec "$@"')
    desired_sans = sans
    if alias and sans == "DNS:findme-photo.ru":
        desired_sans = "DNS:findme-photo.ru, DNS:www.findme-photo.ru"
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": alias,
        "EXPECTED_PUBLIC_IPV4": "111.88.151.64",
        "A_RECORD": a_record,
        "AAAA_RECORD": aaaa_record,
        "REDIRECT_CODE": redirect_code,
        "REDIRECT_URL": redirect_url,
        "HEALTH_CODE": health_code,
        "SERVED_SANS": desired_sans,
    }


def test_public_verify_skips_alias_commands_when_alias_is_empty(
    tmp_path: Path,
    fake_bin: Path,
) -> None:
    env = _public_verify_env(tmp_path, fake_bin)

    result = _run_script("deploy/verify-public-edge.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert "www.findme-photo.ru" not in (tmp_path / "public.log").read_text(encoding="utf-8")


def test_public_verify_accepts_exact_dns_redirect_health_and_sans(
    tmp_path: Path,
    fake_bin: Path,
) -> None:
    env = _public_verify_env(
        tmp_path,
        fake_bin,
        alias="www.findme-photo.ru",
        redirect_url="https://findme-photo.ru/__edge_verify__?source=github",
    )

    result = _run_script("deploy/verify-public-edge.sh", env=env)

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "public.log").read_text(encoding="utf-8")
    assert "dns.google/resolve" in commands
    assert "www.findme-photo.ru" in commands
    assert "http://findme-photo.ru/__edge_verify__?source=github" in commands
    assert "http://www.findme-photo.ru/__edge_verify__?source=github" in commands
    assert "https://findme-photo.ru/health/" in commands
    assert "s_client -connect www.findme-photo.ru:443" in commands


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"a_record": "203.0.113.10"}, "A records"),
        ({"aaaa_record": "2001:db8::1"}, "AAAA records"),
        ({"redirect_code": "301"}, "HTTP redirect"),
        ({"redirect_url": "https://findme-photo.ru/wrong"}, "Location"),
        ({"health_code": "503"}, "HTTPS health"),
        ({"sans": "DNS:findme-photo.ru, DNS:extra.example"}, "certificate SAN"),
    ],
)
def test_public_verify_rejects_inexact_public_state(
    tmp_path: Path,
    fake_bin: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    env = _public_verify_env(tmp_path, fake_bin, **overrides)

    result = _run_script("deploy/verify-public-edge.sh", env=env)

    assert result.returncode != 0
    assert message in result.stderr
