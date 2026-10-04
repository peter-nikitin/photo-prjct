from __future__ import annotations

import os
import subprocess
import sys

import yaml

from tests.deployment.test_deployment_scripts import ROOT


def _render_services(*compose_files: str, environment: dict[str, str] | None = None) -> dict:
    command = ["docker", "compose", "--env-file", ".env.example"]
    for path in compose_files:
        command.extend(("-f", path))
    command.extend(("--profile", "commerce", "config"))
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **(environment or {})},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)["services"]


def _isolated_service_checks(environment: dict[str, str], *, models_only: bool = False):
    script = """
import json
from unittest.mock import patch
from django.core.checks import Tags, run_checks
with patch('environ.Env.read_env'):
    import django
    django.setup()
errors = run_checks(tags=[Tags.models] if __import__('sys').argv[1] == 'models' else None)
print(json.dumps([error.id for error in errors]))
"""
    return subprocess.run(
        [sys.executable, "-c", script, "models" if models_only else "full"],
        cwd=ROOT,
        env={
            **{name: str(value) for name, value in environment.items()},
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONPATH": str(ROOT / "src/backend"),
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_canonical_commerce_starts_with_narrow_environment_and_web_checks_feedback() -> None:
    services = _render_services(
        "docker-compose.deployment.yml",
        "docker-compose.https.yml",
        environment={
            "PRIVATE_MEDIA_S3_BUCKET": "test-private",
            "SELFIE_FEEDBACK_S3_BUCKET": "test-feedback",
            "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "test-feedback-access",
            "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "test-feedback-secret",
            "SELFIE_FEEDBACK_KMS_KEY_ID": "test-feedback-kms",
        },
    )
    commerce = services["commerce-worker"]["environment"]
    web = services["web"]["environment"]
    assert not any(name.startswith("SELFIE_FEEDBACK_") for name in commerce)
    commerce_startup = _isolated_service_checks(commerce, models_only=True)
    assert commerce_startup.returncode == 0, commerce_startup.stderr

    web_startup = _isolated_service_checks(web)
    assert web_startup.returncode == 0, web_startup.stderr
    assert "selfie_search.E008" not in web_startup.stdout
    assert "selfie_search.E009" not in web_startup.stdout

    missing_feedback = {
        name: value for name, value in web.items() if not name.startswith("SELFIE_FEEDBACK_")
    }
    web_rejected = _isolated_service_checks(missing_feedback)
    assert web_rejected.returncode == 0, web_rejected.stderr
    assert "selfie_search.E009" in web_rejected.stdout


def test_disposable_web_stacks_supply_separate_feedback_storage() -> None:
    for compose_file, interpolation in (
        (
            "docker-compose.bib-local.yml",
            {
                "BIB_EVENT_SLUG": "disposable",
                "BIB_SOURCE_ROOT": "/tmp",
                "BIB_BASELINE": "/tmp/baseline.json",
            },
        ),
        ("tests/deployment/import_acceptance/compose.yml", {}),
    ):
        services = _render_services(compose_file, environment=interpolation)
        web = services["web"]["environment"]
        assert web["SELFIE_FEEDBACK_S3_BUCKET"] != web["PRIVATE_MEDIA_S3_BUCKET"]
        for name in (
            "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID",
            "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY",
            "SELFIE_FEEDBACK_KMS_KEY_ID",
        ):
            assert web[name]
        startup = _isolated_service_checks(web)
        assert startup.returncode == 0, startup.stderr
        assert "selfie_search.E008" not in startup.stdout
        assert "selfie_search.E009" not in startup.stdout


def test_deployment_commerce_worker_bypasses_the_web_entrypoint() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.deployment.yml",
            "--profile",
            "commerce",
            "config",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "APP_ENV_FILE": ".env.example",
            "APP_IMAGE": "review-app-image",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commerce_worker = yaml.safe_load(result.stdout)["services"]["commerce-worker"]
    assert commerce_worker["entrypoint"] == ["python", "manage.py", "run_commerce_worker"]
    assert commerce_worker["command"] == []


def test_base_local_commerce_worker_bypasses_the_web_entrypoint() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.yml",
            "--profile",
            "commerce",
            "config",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commerce_worker = yaml.safe_load(result.stdout)["services"]["commerce-worker"]
    assert commerce_worker["entrypoint"] == ["python", "manage.py", "run_commerce_worker"]
    assert commerce_worker["command"] == []


def test_local_purchase_compose_exposes_only_review_ports_and_all_workers() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.local-purchase.yml",
            "--profile",
            "worker",
            "--profile",
            "commerce",
            "config",
        ],
        cwd=ROOT,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "paid-photo-purchase-review"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    compose = yaml.safe_load(result.stdout)
    assert compose["name"] == "paid-photo-purchase-review"
    for service, port in (("web", "8000"), ("minio", "19000"), ("mailpit", "8025")):
        published = compose["services"][service]["ports"][0]
        assert published["host_ip"] == "127.0.0.1"
        assert published["published"] == port
    web = compose["services"]["web"]
    assert web["entrypoint"][:2] == ["/bin/sh", "-ec"]
    assert [line.strip() for line in web["entrypoint"][2].splitlines() if line.strip()] == [
        "python manage.py migrate --noinput",
        "python manage.py sync_feature_flags",
        "python manage.py bootstrap_photographer_group",
        "python manage.py bootstrap_local_purchase_review",
        "python manage.py collectstatic --noinput",
        "exec python manage.py runserver 0.0.0.0:8000",
    ]
    assert web["environment"]["PRIVATE_MEDIA_S3_BUCKET"] == "local-private"
    assert web["environment"]["SELFIE_FEEDBACK_S3_BUCKET"] == "disposable-purchase-feedback"
    assert (
        web["environment"]["SELFIE_FEEDBACK_S3_BUCKET"]
        != web["environment"]["PRIVATE_MEDIA_S3_BUCKET"]
    )
    startup = _isolated_service_checks(web["environment"])
    assert startup.returncode == 0, startup.stderr
    assert "selfie_search.E008" not in startup.stdout
    assert "selfie_search.E009" not in startup.stdout
    assert web["environment"]["PHOTO_PROCESSING_WORKER_TOKEN"] == "local-photo-worker-token"
    assert web["environment"]["MEDIA_S3_ENDPOINT_URL"] == "http://minio.localhost:19000"
    assert web["extra_hosts"] == ["minio.localhost=host-gateway"]
    assert "127.0.0.1:8000/health/" in " ".join(web["healthcheck"]["test"])
    assert (
        compose["services"]["minio"]["environment"]["MINIO_API_CORS_ALLOW_ORIGIN"]
        == "http://127.0.0.1:8000,http://localhost:8000"
    )
    worker = compose["services"]["worker-bulk"]
    assert (
        "2/generate_watermarked_preview/1"
        in worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"]
    )
    assert worker["environment"]["PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO"] == "true"
    assert worker["depends_on"]["web"]["condition"] == "service_healthy"
    selfie_worker = compose["services"]["worker-selfie"]
    assert selfie_worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"] == ("1/selfie_query/2")
    assert selfie_worker["environment"]["PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO"] == "true"
    commerce_worker = compose["services"]["commerce-worker"]
    assert commerce_worker["environment"]["COMMERCE_WORKER_FACTORY"] == (
        "commerce.runtime.commerce_worker_factory"
    )
    assert commerce_worker["environment"]["COMMERCE_SMTP_HOST"] == "mailpit"
    assert commerce_worker["depends_on"]["web"]["condition"] == "service_healthy"


def test_local_purchase_make_targets_are_present() -> None:
    result = subprocess.run(
        ["make", "-n", "local-purchase-up"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "docker-compose.local-purchase.yml" in result.stdout
    assert "--profile worker --profile commerce" in result.stdout
