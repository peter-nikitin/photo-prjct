"""Executable contracts for the one-VM recovery runtime."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from config import settings as production_settings
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import call_command
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.test import RequestFactory, override_settings

ROOT = Path(__file__).resolve().parents[2]
WEB_REQUIRED = {
    "SECRET_KEY": "local-test-key",
    "DEBUG": "False",
    "ALLOWED_HOSTS": "192.0.2.44,web,localhost",
    "DB_NAME": "app",
    "DB_USER": "app",
    "DB_PASSWORD": "local-test-only",
    "GUNICORN_WORKERS": "1",
    "GUNICORN_THREADS": "2",
    "GUNICORN_TIMEOUT": "30",
    "GUNICORN_MAX_REQUESTS": "100",
    "GUNICORN_MAX_REQUESTS_JITTER": "10",
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


def test_database_only_compose_accepts_only_database_values(tmp_path: Path):
    env_file = tmp_path / "db.env"
    env_file.write_text("DB_NAME=app\nDB_USER=app\nDB_PASSWORD=local-test-only\n")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "docker-compose.deployment.yml"),
            "config",
            "--quiet",
        ],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_web_entrypoint_collects_static_before_gunicorn(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python", "gunicorn"):
        command = fake_bin / name
        command.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "$COMMAND_LOG"\n')
        command.chmod(0o755)
    log = tmp_path / "commands"
    result = subprocess.run(
        ["sh", str(ROOT / "src/backend/entrypoint.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
            **WEB_REQUIRED,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "python manage.py collectstatic --noinput",
        (
            "gunicorn config.wsgi:application --config python:config.gunicorn "
            "--bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 30 "
            "--max-requests 100 --max-requests-jitter 10"
        ),
    ]


def test_web_entrypoint_refuses_missing_application_secrets(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python", "gunicorn"):
        command = fake_bin / name
        command.write_text('#!/bin/sh\nprintf "%s\\n" "called" >> "$COMMAND_LOG"\n')
        command.chmod(0o755)
    log = tmp_path / "commands"
    result = subprocess.run(
        ["sh", str(ROOT / "src/backend/entrypoint.sh")],
        env={"PATH": f"{fake_bin}:{os.environ['PATH']}", "COMMAND_LOG": str(log)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr
    assert not log.exists()


def test_image_origin_refuses_missing_s3_credentials():
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/image-origin/imgproxy-start.sh")],
        env={
            "PATH": os.environ["PATH"],
            "IMGPROXY_KEY": "local-test-key",
            "IMGPROXY_SALT": "local-test-salt",
            "IMGPROXY_S3_ALLOWED_BUCKETS": "private-local-test",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "AWS_ACCESS_KEY_ID" in result.stderr


@pytest.mark.django_db
def test_production_catalog_and_manifest_css_after_collectstatic(tmp_path: Path, client):
    with override_settings(DEBUG=False, STATIC_ROOT=tmp_path / "staticfiles"):
        call_command("collectstatic", interactive=False, verbosity=0)
        page = client.get("/", HTTP_HOST="localhost")
        assert page.status_code == 200
        css_url = re.search(rb'href="(/static/ui/catalog\.[^"]+\.css)"', page.content)
        assert css_url is not None
        css = client.get(css_url.group(1).decode(), HTTP_HOST="localhost")
        assert css.status_code == 200
        assert b".catalog" in b"".join(css.streaming_content)


@pytest.mark.django_db
def test_edge_health_uses_documented_localhost_host(client):
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    assert "http://127.0.0.1:8080/ready/" in services["edge"]["healthcheck"]["test"]
    assert "Host: localhost" in services["edge"]["healthcheck"]["test"]
    with override_settings(DEBUG=False, ALLOWED_HOSTS=["192.0.2.44", "web", "localhost"]):
        assert client.get("/ready/", HTTP_HOST="localhost").status_code == 200
        assert client.get("/ready/", HTTP_HOST="127.0.0.1").status_code == 400


def test_bootstrap_compose_keeps_postgres_on_host_disk_and_workers_dormant():
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]

    assert services["db"]["volumes"] == ["/var/lib/findme-photo/postgres:/var/lib/postgresql/data"]
    assert services["web"]["image"].startswith("${APP_IMAGE:")
    assert services["photo-worker"]["profiles"] == ["processing"]
    assert services["import-worker"]["profiles"] == ["import"]
    assert services["commerce-worker"]["profiles"] == ["commerce"]
    assert services["photo-worker"]["environment"]["PHOTO_WORKER_API_URL"] == (
        "http://web:8000/internal/photo-processing/v1/"
    )
    assert services["photo-worker"]["environment"]["PHOTO_WORKER_TRANSPORT"] == "local"
    assert services["photo-worker"]["environment"]["PHOTO_WORKER_TOKEN"] == (
        "${PHOTO_PROCESSING_WORKER_TOKEN:-}"
    )
    assert "DB_PASSWORD" not in services["photo-worker"]["environment"]
    assert not services["web"].get("ports")
    assert not services["db"].get("ports")


def test_http_edge_exposes_only_public_django_and_signed_images():
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    assert services["edge"]["ports"] == ["80:8080"]
    config = (ROOT / "deploy/nginx/recovery-http.conf").read_text()
    assert "location ^~ /internal/" in config
    assert "proxy_pass http://web:8000" in config
    assert "proxy_pass http://imgproxy:8080" in config


def test_production_cookies_are_secure_and_visual_loopback_is_explicit() -> None:
    from tests.visual import settings as visual_settings

    assert production_settings.SESSION_COOKIE_SECURE is True
    assert production_settings.CSRF_COOKIE_SECURE is True
    assert visual_settings.SESSION_COOKIE_SECURE is False
    assert visual_settings.CSRF_COOKIE_SECURE is False

    request = RequestFactory().get("/")
    with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies"):
        session = SessionMiddleware(lambda req: HttpResponse())
        session.process_request(request)
        request.session["synthetic"] = "fixture"
        response = session.process_response(request, HttpResponse())
    assert response.cookies[settings.SESSION_COOKIE_NAME]["secure"] is True

    request = RequestFactory().get("/")
    csrf = CsrfViewMiddleware(lambda req: HttpResponse())
    csrf.process_request(request)
    get_token(request)
    response = csrf.process_response(request, HttpResponse())
    assert response.cookies[settings.CSRF_COOKIE_NAME]["secure"] is True


def test_deploy_updates_only_selected_image_and_never_restores(tmp_path: Path):
    root = tmp_path / "release"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text(
        "APP_IMAGE=ghcr.io/example/app:" + "a" * 40 + "\n"
        "PHOTO_WORKER_IMAGE=ghcr.io/example/worker:" + "b" * 40 + "\n"
        "IMPORT_WORKER_IMAGE=ghcr.io/example/import:" + "c" * 40 + "\n"
        "PHOTO_WORKER_ENABLED=True\nPHOTO_PROCESSING_ENABLED=True\n"
        "PHOTO_PROCESSING_WORKER_TOKEN=local-token\n",
    )
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$COMMAND_LOG"\n')
    docker.chmod(0o755)
    log = tmp_path / "commands"
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
            "DEPLOY_ROOT": str(root),
            "DEPLOY_WEB": "false",
            "DEPLOY_PHOTO": "true",
            "DEPLOY_IMPORT": "false",
            "DEPLOY_COMMERCE": "false",
            "PHOTO_WORKER_IMAGE": "ghcr.io/example/worker:" + "d" * 40,
            "PHOTO_WORKER_BUILD": "d" * 40,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "APP_IMAGE=ghcr.io/example/app:" + "a" * 40 in env_file.read_text()
    assert "PHOTO_WORKER_IMAGE=ghcr.io/example/worker:" + "d" * 40 in env_file.read_text()
    commands = log.read_text()
    assert "pull photo-worker" in commands
    assert "up -d --wait --no-deps photo-worker" in commands
    assert "restore" not in commands
    assert "web" not in commands


def test_deploy_refuses_web_migration_without_restored_schema(tmp_path: Path):
    root = tmp_path / "release"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text("APP_IMAGE=ghcr.io/example/app:" + "a" * 40 + "\n")
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$COMMAND_LOG"\n'
        'case "$*" in *"exec -T db"*) printf \'missing\\n\';; esac\n'
    )
    docker.chmod(0o755)
    log = tmp_path / "commands"
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
            "DEPLOY_ROOT": str(root),
            "DEPLOY_WEB": "true",
            "DEPLOY_PHOTO": "false",
            "DEPLOY_IMPORT": "false",
            "DEPLOY_COMMERCE": "false",
            "APP_IMAGE": "ghcr.io/example/app:" + "e" * 40,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "restore" in result.stderr.lower()
    assert "manage.py migrate" not in log.read_text()
    assert env_file.read_text() == "APP_IMAGE=ghcr.io/example/app:" + "a" * 40 + "\n"


def test_failed_image_pull_preserves_previous_root_private_environment(tmp_path: Path):
    root = tmp_path / "release"
    root.mkdir()
    env_file = root / ".env"
    original = "APP_IMAGE=ghcr.io/example/app:" + "a" * 40 + "\nPRIVATE_SECRET=keep-me\n"
    env_file.write_text(original)
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\ncase "$*" in *"exec -T db"*) printf \'t\\n\';; *"pull web"*) exit 1;; esac\n'
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(root),
            "DEPLOY_WEB": "true",
            "APP_IMAGE": "ghcr.io/example/app:" + "e" * 40,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert env_file.read_text() == original
    assert env_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fail_pull", (False, True))
def test_pending_environment_is_private_at_first_write(tmp_path: Path, fail_pull: bool):
    root = tmp_path / "release"
    root.mkdir()
    env_file = root / ".env"
    original = "PRIVATE_SECRET=local-test-secret\n"
    env_file.write_text(original)
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\ncase "$*" in *"pull photo-worker"*) exit "$FAIL_PULL";; esac\n')
    docker.chmod(0o755)
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "_open = Path.open\n"
        "_fdopen = os.fdopen\n"
        "def _record(mode):\n"
        "    Path(os.environ['MODE_LOG']).write_text(str(mode & 0o777))\n"
        "def path_open(self, mode='r', *args, **kwargs):\n"
        "    result = _open(self, mode, *args, **kwargs)\n"
        "    if self.name.startswith('.env.pending') and 'w' in mode:\n"
        "        _record(self.stat().st_mode)\n"
        "    return result\n"
        "def fdopen(fd, mode='r', *args, **kwargs):\n"
        "    if 'w' in mode:\n"
        "        _record(os.fstat(fd).st_mode)\n"
        "    return _fdopen(fd, mode, *args, **kwargs)\n"
        "Path.open = path_open\n"
        "os.fdopen = fdopen\n"
    )
    mode_log = tmp_path / "mode"
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PYTHONPATH": str(hook_dir),
            "MODE_LOG": str(mode_log),
            "FAIL_PULL": "1" if fail_pull else "0",
            "DEPLOY_ROOT": str(root),
            "DEPLOY_PHOTO": "true",
            "PHOTO_WORKER_IMAGE": "ghcr.io/example/worker:" + "e" * 40,
            "PHOTO_WORKER_BUILD": "e" * 40,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == (1 if fail_pull else 0), result.stderr
    assert mode_log.read_text() == str(0o600)
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert not list(root.glob(".env.pending.*"))
    if fail_pull:
        assert env_file.read_text() == original


def test_selected_bank_requires_complete_merchant_settings_before_image_change(tmp_path: Path):
    root = tmp_path / "release"
    root.mkdir()
    original = (
        "APP_IMAGE=ghcr.io/example/app:" + "a" * 40 + "\n"
        "COMMERCE_PAYMENT_GATEWAY_FACTORY=commerce.tbank_gateway.tbank_gateway_factory\n"
        "COMMERCE_PUBLIC_ORIGIN=https://findme-photo.ru\n"
    )
    env_file = root / ".env"
    env_file.write_text(original)
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\ncase "$*" in *"exec -T db"*) printf \'t\\n\';; esac\n')
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(root),
            "DEPLOY_WEB": "true",
            "APP_IMAGE": "ghcr.io/example/app:" + "e" * 40,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "TBANK_TERMINAL_KEY" in result.stderr
    assert env_file.read_text() == original


@pytest.mark.parametrize("worker", ("PHOTO", "IMPORT"))
def test_active_worker_requires_its_separate_api_token(tmp_path: Path, worker: str):
    root = tmp_path / "release"
    root.mkdir()
    env_file = root / ".env"
    enabled_key = "PHOTO_WORKER_ENABLED" if worker == "PHOTO" else "PHOTO_IMPORT_WORKER_ENABLED"
    env_file.write_text(f"{enabled_key}=True\n")
    env_file.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(root),
            "DEPLOY_PHOTO": "true" if worker == "PHOTO" else "false",
            "DEPLOY_IMPORT": "true" if worker == "IMPORT" else "false",
            "PHOTO_WORKER_IMAGE": "ghcr.io/example/worker:" + "a" * 40,
            "PHOTO_WORKER_BUILD": "a" * 40,
            "IMPORT_WORKER_IMAGE": "ghcr.io/example/import:" + "a" * 40,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "TOKEN" in result.stderr
