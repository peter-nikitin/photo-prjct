"""Repository contract for the separately packaged local worker."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

import yaml
from django.test import Client, override_settings

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_SETTINGS = {
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "SECRET_KEY",
    "MEDIA_S3_ACCESS_KEY_ID",
    "MEDIA_S3_SECRET_ACCESS_KEY",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY",
}


def test_worker_container_is_minimal_and_starts_the_standalone_package() -> None:
    """A future image change must not accidentally package Django with the worker."""
    dockerfile = (ROOT / "Dockerfile.worker").read_text(encoding="utf-8")

    assert "COPY src/worker/requirements.txt" in dockerfile
    assert "COPY src/worker/photo_worker" in dockerfile
    assert 'CMD ["python", "-m", "photo_worker"]' in dockerfile
    assert "USER worker" in dockerfile
    assert "src/backend" not in dockerfile
    assert "manage.py" not in dockerfile
    assert "django" not in dockerfile.lower()


def test_worker_compose_profile_is_opt_in_and_receives_only_its_narrow_contract() -> None:
    """A compose edit must not hand the worker application or permanent-storage credentials."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]
    environment = worker["environment"]

    assert worker["profiles"] == ["worker"]
    assert worker["build"]["dockerfile"] == "Dockerfile.worker"
    assert worker.get("env_file") is None
    assert set(environment) == {
        "PHOTO_WORKER_API_URL",
        "PHOTO_WORKER_TOKEN",
        "PHOTO_WORKER_BUILD",
        "PHOTO_WORKER_LEASE_SECONDS",
    }
    assert environment["PHOTO_WORKER_API_URL"].endswith("/internal/photo-processing/v1")
    assert "PHOTO_PROCESSING_WORKER_TOKEN" in environment["PHOTO_WORKER_TOKEN"]
    assert not (FORBIDDEN_SETTINGS & set(environment))
    assert "PHOTO_WORKER_CONCURRENCY" not in environment


def test_production_worker_profile_is_bounded_and_isolated_from_web_configuration() -> None:
    """The deployed worker has only its private API contract and declared resource bounds."""
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert worker["image"] == "${WORKER_IMAGE:-}"
    assert worker["profiles"] == ["worker"]
    assert worker.get("ports") is None
    assert worker.get("env_file") is None
    assert worker["depends_on"] == {"web": {"condition": "service_healthy"}}
    assert worker["restart"] == "unless-stopped"
    assert worker["cpus"] == "1.0"
    assert worker["mem_limit"] == "768m"
    assert worker["pids_limit"] == 64
    assert worker["environment"] == {
        "PHOTO_WORKER_API_URL": "http://web:8000/internal/photo-processing/v1",
        "PHOTO_WORKER_TOKEN": "${PHOTO_PROCESSING_WORKER_TOKEN:-}",
        "PHOTO_WORKER_BUILD": "${PHOTO_WORKER_BUILD:-capture-metadata-v1}",
        "PHOTO_WORKER_LEASE_SECONDS": "${PHOTO_WORKER_LEASE_SECONDS:-120}",
    }
    assert not (FORBIDDEN_SETTINGS & set(worker["environment"]))


def test_default_compose_config_interpolates_example_without_enabling_the_worker() -> None:
    """An inactive profile must not make the normal local Compose configuration unusable."""
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        _copy_compose_inputs(directory)
        result = _compose_config(directory, "--env-file", ".env.example")

        assert result.returncode == 0, result.stderr
        compose = yaml.safe_load(result.stdout)
        assert "worker" not in compose["services"]

        worker_profile = _compose_config(
            directory, "--env-file", ".env.example", "--profile", "worker"
        )
        assert worker_profile.returncode == 0, worker_profile.stderr
        worker = yaml.safe_load(worker_profile.stdout)["services"]["worker"]
        assert worker["environment"]["PHOTO_WORKER_TOKEN"] == ""
        assert not (FORBIDDEN_SETTINGS & set(worker["environment"]))


def test_copied_and_edited_dotenv_remains_the_web_service_environment() -> None:
    """The copyable template must not redirect web away from a developer's edited `.env`."""
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        _copy_compose_inputs(directory)
        dotenv = directory / ".env"
        dotenv.write_text(
            (directory / ".env.example")
            .read_text(encoding="utf-8")
            .replace("SECRET_KEY=change-me", "SECRET_KEY=edited-local-secret"),
            encoding="utf-8",
        )

        result = _compose_config(directory)

    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(result.stdout)["services"]["web"]["environment"]["SECRET_KEY"] == (
        "edited-local-secret"
    )


def test_worker_compose_hostname_is_accepted_by_django_tracked_defaults() -> None:
    """The private Compose hostname must pass Django's host validation before API auth runs."""
    values = _dotenv_values(ROOT / ".env.example")
    allowed_hosts = values["ALLOWED_HOSTS"].split(",")

    assert "web" in allowed_hosts
    with override_settings(ALLOWED_HOSTS=allowed_hosts):
        response = Client().get("/health/", HTTP_HOST="web")
    assert response.status_code == 200


def _copy_compose_inputs(destination: Path) -> None:
    for name in ("docker-compose.yml", ".env.example"):
        copy2(ROOT / name, destination / name)


def _compose_config(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *arguments, "config"],
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
    )


def _dotenv_values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", maxsplit=1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
