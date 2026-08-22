"""Fail-closed deployment checks for the dark Commerce runtime."""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register

COMMERCE_RUNTIME_CHECK_TAG = "commerce_runtime"

_TEST_ADAPTERS = {
    "COMMERCE_PAYMENT_GATEWAY_FACTORY": "commerce.test_payment_gateway.",
    "COMMERCE_EMAIL_SENDER_FACTORY": "commerce.test_email_sender.",
}


@register(COMMERCE_RUNTIME_CHECK_TAG)
def check_commerce_runtime_settings(
    app_configs: Iterable[AppConfig] | None,
    **kwargs: Any,
) -> list[Error]:
    """Keep deterministic adapters local even while the database gate is dark."""
    del app_configs, kwargs
    errors: list[Error] = []
    if settings.DEBUG is not True:
        for setting_name, test_module in _TEST_ADAPTERS.items():
            configured = getattr(settings, setting_name, "")
            if isinstance(configured, str) and configured.startswith(test_module):
                errors.append(
                    Error(
                        f"{setting_name} must not select a local/test Commerce adapter "
                        "when DEBUG is False.",
                        id=(
                            "commerce.E001"
                            if setting_name == "COMMERCE_PAYMENT_GATEWAY_FACTORY"
                            else "commerce.E002"
                        ),
                    )
                )
    threshold = getattr(settings, "COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS", None)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or not 1 <= threshold <= 3600:
        errors.append(
            Error(
                "COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS must be between 1 and 3600.",
                id="commerce.E003",
            )
        )
    return errors
