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
        "WORKER_IMAGE": "review-worker-image",
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
            "worker",
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
    return yaml.safe_load(result.stdout)


def test_gallery_delivery_configuration_changes_only_the_web_environment() -> None:
    """A deployment setting must not alter worker topology or grant it signing material."""
    configured = _render_all_deployment_profiles(GALLERY_DELIVERY_ENVIRONMENT)
    dark = _render_all_deployment_profiles(
        {
            "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
            "GALLERY_CDN_TOKEN_SECRET": "",
            "GALLERY_IMGPROXY_KEY": "",
            "GALLERY_IMGPROXY_SALT": "",
        }
    )

    configured_web = configured["services"]["web"]["environment"]
    assert {name: configured_web[name] for name in GALLERY_DELIVERY_ENVIRONMENT} == (
        GALLERY_DELIVERY_ENVIRONMENT
    )
    assert {
        name: dark["services"]["web"]["environment"][name] for name in GALLERY_DELIVERY_ENVIRONMENT
    } == {
        "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
        "GALLERY_CDN_TOKEN_SECRET": "",
        "GALLERY_IMGPROXY_KEY": "",
        "GALLERY_IMGPROXY_SALT": "",
    }

    configured_without_gallery = configured.copy()
    configured_without_gallery["services"] = configured["services"].copy()
    configured_without_gallery["services"]["web"] = configured["services"]["web"].copy()
    configured_without_gallery["services"]["web"]["environment"] = configured_web.copy()
    dark_without_gallery = dark.copy()
    dark_without_gallery["services"] = dark["services"].copy()
    dark_without_gallery["services"]["web"] = dark["services"]["web"].copy()
    dark_without_gallery["services"]["web"]["environment"] = dark["services"]["web"][
        "environment"
    ].copy()
    for name in GALLERY_DELIVERY_ENVIRONMENT:
        configured_without_gallery["services"]["web"]["environment"].pop(name)
        dark_without_gallery["services"]["web"]["environment"].pop(name)
    assert configured_without_gallery == dark_without_gallery

    for service_name, service in configured["services"].items():
        if service_name == "web":
            continue
        environment = service.get("environment") or {}
        assert not GALLERY_DELIVERY_ENVIRONMENT.keys() & environment.keys(), service_name


def test_deployment_compose_projects_postbox_credentials_only_to_commerce_worker() -> None:
    environment = {
        **os.environ,
        "APP_IMAGE": "review-app-image",
        "WORKER_IMAGE": "review-worker-image",
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
            "worker",
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

    for service_name in ("db", "worker-bulk", "worker-selfie"):
        assert "COMMERCE_POSTBOX_API_KEY_ID" not in (
            compose["services"][service_name].get("environment") or {}
        )
        assert "COMMERCE_POSTBOX_API_KEY_SECRET" not in (
            compose["services"][service_name].get("environment") or {}
        )
