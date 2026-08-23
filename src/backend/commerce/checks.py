"""Fail-closed deployment checks for the dark Commerce runtime."""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

COMMERCE_RUNTIME_CHECK_TAG = "commerce_runtime"

_TEST_ADAPTERS = {
    "COMMERCE_PAYMENT_GATEWAY_FACTORY": "commerce.test_payment_gateway.",
    "COMMERCE_EMAIL_SENDER_FACTORY": "commerce.test_email_sender.",
}
_POSTBOX_EMAIL_SENDER_FACTORY = "commerce.postbox_email_sender.postbox_email_sender_factory"


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
        if getattr(settings, "COMMERCE_WORKER_ENABLED", False) is True:
            if settings.COMMERCE_EMAIL_SENDER_FACTORY != _POSTBOX_EMAIL_SENDER_FACTORY:
                errors.append(
                    Error(
                        "An enabled deployed Commerce worker must select the Postbox email "
                        "sender factory.",
                        id="commerce.E004",
                    )
                )
            sender_address = getattr(settings, "COMMERCE_EMAIL_FROM_ADDRESS", "")
            try:
                validate_email(sender_address)
                if sender_address.casefold().endswith("@localhost"):
                    raise ValidationError("Local sender identities are not deployable.")
            except (TypeError, ValidationError):
                errors.append(
                    Error(
                        "An enabled deployed Commerce worker requires a valid non-local "
                        "email sender address.",
                        id="commerce.E005",
                    )
                )
            if not _is_configured_secret(getattr(settings, "COMMERCE_POSTBOX_API_KEY_ID", "")):
                errors.append(
                    Error(
                        "An enabled deployed Commerce worker requires a Postbox API-key ID.",
                        id="commerce.E006",
                    )
                )
            if not _is_configured_secret(getattr(settings, "COMMERCE_POSTBOX_API_KEY_SECRET", "")):
                errors.append(
                    Error(
                        "An enabled deployed Commerce worker requires a Postbox API-key secret.",
                        id="commerce.E007",
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


def _is_configured_secret(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
