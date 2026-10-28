from __future__ import annotations

import os
import subprocess

import yaml

from tests.deployment.test_deployment_scripts import ROOT

GALLERY_DELIVERY_ENVIRONMENT = {
    "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
    "GALLERY_CDN_TOKEN_SECRET": "cdn-token-secret",
    "GALLERY_IMGPROXY_KEY": "11" * 32,
    "GALLERY_IMGPROXY_SALT": "22" * 32,
}


def _render_all_deployment_profiles(extra_environment: dict[str, str]) -> dict:
    environment = {
        **os.environ,
        "APP_IMAGE": "review-app-image",
        "IMPORT_WORKER_IMAGE": "review-import-image",
        "SECRET_KEY": "secret-key",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "findme-photo.ru,web",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "db-password",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "COMMERCE_WORKER_ENABLED": "False",
        **extra_environment,
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.deployment.yml",
            "-f",
            "docker-compose.https.yml",
            "--profile",
            "import",
            "--profile",
            "processing",
            "--profile",
            "commerce",
            "config",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


def test_gallery_delivery_keeps_cdn_secret_on_web_and_source_keys_on_imgproxy() -> None:
    configured = _render_all_deployment_profiles(GALLERY_DELIVERY_ENVIRONMENT)["services"]
    web = configured["web"]["environment"]
    imgproxy = configured["imgproxy"]["environment"]
    for name, value in GALLERY_DELIVERY_ENVIRONMENT.items():
        assert web[name] == value
    assert imgproxy["IMGPROXY_KEY"] == GALLERY_DELIVERY_ENVIRONMENT["GALLERY_IMGPROXY_KEY"]
    assert imgproxy["IMGPROXY_SALT"] == GALLERY_DELIVERY_ENVIRONMENT["GALLERY_IMGPROXY_SALT"]
    for name in ("photo-worker", "import-worker", "commerce-worker", "edge"):
        environment = configured[name].get("environment") or {}
        assert "GALLERY_CDN_TOKEN_SECRET" not in environment
        assert "GALLERY_IMGPROXY_KEY" not in environment
        assert "GALLERY_IMGPROXY_SALT" not in environment


def test_deployment_compose_projects_postbox_credentials_only_to_commerce_worker() -> None:
    environment = {
        **os.environ,
        "APP_IMAGE": "review-app-image",
        "IMPORT_WORKER_IMAGE": "review-import-image",
        "SECRET_KEY": "secret-key",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "findme-photo.ru,web",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "db-password",
        "PUBLIC_DOMAIN": "findme-photo.ru",
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
        "COMMERCE_POSTBOX_API_KEY_SECRET": "postbox-api-key-secret",
        "COMMERCE_ORDER_ACCESS_SIGNING_SECRET": "commerce-signing-secret",
        "COMMERCE_SUPPORT_CONTACT": "support@findme-photo.ru",
        "COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS": "777",
    }

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.deployment.yml",
            "--profile",
            "import",
            "--profile",
            "commerce",
            "config",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    compose = yaml.safe_load(result.stdout)
    web_environment = compose["services"]["web"]["environment"]
    commerce_environment = compose["services"]["commerce-worker"]["environment"]

    assert "env_file" not in compose["services"]["web"]
    assert web_environment["COMMERCE_PUBLIC_ORIGIN"] == "https://findme-photo.ru"
    assert web_environment["COMMERCE_PAYMENT_GATEWAY_FACTORY"] == (
        "commerce.payment_simulator.payment_simulator_gateway_factory"
    )
    assert web_environment["COMMERCE_ORDER_ACCESS_SIGNING_SECRET"] == "commerce-signing-secret"
    assert web_environment["COMMERCE_SUPPORT_CONTACT"] == "support@findme-photo.ru"
    assert web_environment["COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS"] == "777"
    assert "COMMERCE_POSTBOX_API_KEY_ID" not in web_environment
    assert "COMMERCE_POSTBOX_API_KEY_SECRET" not in web_environment

    assert commerce_environment["COMMERCE_EMAIL_FROM_ADDRESS"] == "orders@findme-photo.ru"
    assert commerce_environment["COMMERCE_POSTBOX_API_KEY_ID"] == "postbox-api-key-id"
    assert commerce_environment["COMMERCE_POSTBOX_API_KEY_SECRET"] == "postbox-api-key-secret"
    assert commerce_environment["COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS"] == "777"
    assert commerce_environment["COMMERCE_WORKER_ENABLED"] == "True"

    assert "worker-bulk" not in compose["services"]
    assert "worker-selfie" not in compose["services"]
    for service_name in ("db", "import-worker"):
        assert "COMMERCE_POSTBOX_API_KEY_ID" not in (
            compose["services"][service_name].get("environment") or {}
        )
        assert "COMMERCE_POSTBOX_API_KEY_SECRET" not in (
            compose["services"][service_name].get("environment") or {}
        )
