from __future__ import annotations

import os
import subprocess

import yaml

from tests.deployment.test_deployment_scripts import ROOT


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
    assert web["environment"]["PHOTO_UPLOAD_ENABLED"] == "True"
    assert web["environment"]["PHOTO_PROCESSING_PREVIEW_ENABLED"] == "True"
    assert web["environment"]["MEDIA_S3_ENDPOINT_URL"] == "http://minio.localhost:19000"
    assert web["extra_hosts"] == ["minio.localhost=host-gateway"]
    assert "127.0.0.1:8000/health/" in " ".join(web["healthcheck"]["test"])
    assert (
        compose["services"]["minio"]["environment"]["MINIO_API_CORS_ALLOW_ORIGIN"]
        == "http://127.0.0.1:8000,http://localhost:8000"
    )
    worker = compose["services"]["worker"]
    assert (
        "2/generate_watermarked_preview/1"
        in worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"]
    )
    assert worker["environment"]["PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO"] == "true"
    assert worker["depends_on"]["web"]["condition"] == "service_healthy"
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
