from __future__ import annotations

import os
import subprocess

import yaml

from tests.deployment.test_deployment_scripts import ROOT


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

    for service_name in ("db", "worker"):
        assert "COMMERCE_POSTBOX_API_KEY_ID" not in (
            compose["services"][service_name].get("environment") or {}
        )
        assert "COMMERCE_POSTBOX_API_KEY_SECRET" not in (
            compose["services"][service_name].get("environment") or {}
        )
