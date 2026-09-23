import json
import os
import subprocess
import sys

from django.conf import settings
from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from commerce.checks import COMMERCE_RUNTIME_CHECK_TAG


class CommerceRuntimeSettingsTests(SimpleTestCase):
    def test_malformed_closing_receipt_setting_fails_from_real_environment(self) -> None:
        merchant_env = {
            "DEBUG": "False",
            "PUBLIC_DOMAIN": "findme-photo.ru",
            "COMMERCE_PUBLIC_ORIGIN": "https://findme-photo.ru",
            "COMMERCE_PAYMENT_GATEWAY_FACTORY": "commerce.tbank_gateway.tbank_gateway_factory",
            "TBANK_TERMINAL_KEY": "test-terminal",
            "TBANK_TERMINAL_PASSWORD": "test-password",
            "TBANK_API_ORIGIN": "https://rest-api-test.tinkoff.ru",
            "TBANK_RUB_ONLY": "True",
            "TBANK_PAY_TYPE": "O",
            "TBANK_RECEIPT_FFD": "1.2",
            "TBANK_RECEIPT_TAXATION": "osn",
            "TBANK_RECEIPT_TAX": "none",
            "TBANK_RECEIPT_PAYMENT_METHOD": "full_payment",
            "TBANK_RECEIPT_PAYMENT_OBJECT": "service",
            "TBANK_RECEIPT_MEASUREMENT_UNIT": "шт",
            "TBANK_RECEIPT_CLOSING_REQUIRED": "unapproved",
        }
        program = """
import json
import django
django.setup()
from django.conf import settings
from django.core.checks import run_checks
from commerce.tbank_gateway import tbank_gateway_factory
try:
    tbank_gateway_factory()
    adapter_rejected = False
except ValueError:
    adapter_rejected = True
print(json.dumps({
    "closing_required": settings.TBANK_RECEIPT_CLOSING_REQUIRED,
    "errors": [error.id for error in run_checks(tags=["commerce_runtime"])],
    "adapter_rejected": adapter_rejected,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=settings.BASE_DIR,
            env={**os.environ, **merchant_env, "DJANGO_SETTINGS_MODULE": "config.settings"},
            check=True,
            capture_output=True,
            text=True,
        )
        observation = json.loads(result.stdout)
        self.assertIsNone(observation["closing_required"])
        self.assertIn("commerce.E008", observation["errors"])
        self.assertTrue(observation["adapter_rejected"])

    def test_tbank_settings_default_to_unconfigured(self) -> None:
        for name in (
            "TERMINAL_KEY",
            "TERMINAL_PASSWORD",
            "API_ORIGIN",
            "PAY_TYPE",
            "RECEIPT_FFD",
            "RECEIPT_TAXATION",
            "RECEIPT_TAX",
            "RECEIPT_PAYMENT_METHOD",
            "RECEIPT_PAYMENT_OBJECT",
            "RECEIPT_MEASUREMENT_UNIT",
        ):
            self.assertEqual(getattr(settings, f"TBANK_{name}"), "")
        self.assertIsNone(settings.TBANK_RUB_ONLY)
        self.assertIsNone(settings.TBANK_RECEIPT_CLOSING_REQUIRED)

    @override_settings(
        DEBUG=False,
        COMMERCE_PAYMENT_GATEWAY_FACTORY="commerce.tbank_gateway.tbank_gateway_factory",
    )
    def test_deployed_tbank_selection_rejects_missing_merchant_configuration(self) -> None:
        self.assertIn(
            "commerce.E008", [error.id for error in run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])]
        )

    @override_settings(
        DEBUG=False,
        PUBLIC_DOMAIN="findme-photo.ru",
        COMMERCE_PUBLIC_ORIGIN="https://findme-photo.ru",
        COMMERCE_PAYMENT_GATEWAY_FACTORY="commerce.tbank_gateway.tbank_gateway_factory",
        TBANK_TERMINAL_KEY="test-terminal",
        TBANK_TERMINAL_PASSWORD="test-password",
        TBANK_API_ORIGIN="https://rest-api-test.tinkoff.ru",
        TBANK_RUB_ONLY=True,
        TBANK_PAY_TYPE="O",
        TBANK_RECEIPT_FFD="1.2",
        TBANK_RECEIPT_TAXATION="osn",
        TBANK_RECEIPT_TAX="none",
        TBANK_RECEIPT_PAYMENT_METHOD="full_payment",
        TBANK_RECEIPT_PAYMENT_OBJECT="service",
        TBANK_RECEIPT_MEASUREMENT_UNIT="шт",
        TBANK_RECEIPT_CLOSING_REQUIRED=False,
    )
    def test_deployed_tbank_selection_accepts_complete_merchant_configuration(self) -> None:
        self.assertEqual(run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG]), [])

    @override_settings(
        DEBUG=False,
        COMMERCE_PAYMENT_GATEWAY_FACTORY="commerce.payment_simulator.payment_simulator_gateway_factory",
        TBANK_TERMINAL_KEY="",
    )
    def test_deployed_simulator_remains_valid_without_tbank_configuration(self) -> None:
        self.assertEqual(run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG]), [])

    def test_dark_defaults_keep_every_real_adapter_and_secret_blank(self) -> None:
        self.assertEqual(settings.COMMERCE_PAYMENT_GATEWAY_FACTORY, "")
        self.assertEqual(settings.COMMERCE_EMAIL_SENDER_FACTORY, "")
        self.assertEqual(settings.COMMERCE_WORKER_FACTORY, "")
        self.assertEqual(settings.COMMERCE_ORDER_ACCESS_SIGNING_SECRET, "")
        self.assertEqual(settings.COMMERCE_SUPPORT_CONTACT, "")
        self.assertEqual(settings.COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS, 300)
        self.assertIs(settings.COMMERCE_WORKER_ENABLED, False)
        self.assertEqual(settings.COMMERCE_EMAIL_FROM_ADDRESS, "")
        self.assertEqual(settings.COMMERCE_POSTBOX_API_KEY_ID, "")
        self.assertEqual(settings.COMMERCE_POSTBOX_API_KEY_SECRET, "")
        self.assertEqual(run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG]), [])

    @override_settings(
        DEBUG=True,
        COMMERCE_PAYMENT_GATEWAY_FACTORY="commerce.test_payment_gateway.DeterministicPaymentGateway",
        COMMERCE_EMAIL_SENDER_FACTORY="commerce.test_email_sender.DeterministicEmailSender",
    )
    def test_local_test_adapters_remain_available_in_debug_execution(self) -> None:
        self.assertEqual(run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG]), [])

    @override_settings(
        DEBUG=False,
        COMMERCE_PAYMENT_GATEWAY_FACTORY="commerce.test_payment_gateway.DeterministicPaymentGateway",
    )
    def test_deployed_configuration_rejects_test_payment_adapter_while_purchase_is_off(
        self,
    ) -> None:
        errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

        self.assertEqual([error.id for error in errors], ["commerce.E001"])

    @override_settings(
        DEBUG=False,
        COMMERCE_EMAIL_SENDER_FACTORY="commerce.test_email_sender.DeterministicEmailSender",
    )
    def test_deployed_configuration_rejects_test_email_adapter_while_purchase_is_in_staff_mode(
        self,
    ) -> None:
        errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

        self.assertEqual([error.id for error in errors], ["commerce.E002"])

    @override_settings(
        COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS=0,
    )
    def test_health_threshold_must_be_a_bounded_positive_number(self) -> None:
        errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

        self.assertEqual([error.id for error in errors], ["commerce.E003"])

    @override_settings(
        DEBUG=False,
        COMMERCE_WORKER_ENABLED=True,
        COMMERCE_EMAIL_SENDER_FACTORY=(
            "commerce.postbox_email_sender.postbox_email_sender_factory"
        ),
        COMMERCE_EMAIL_FROM_ADDRESS="orders@findme-photo.ru",
        COMMERCE_POSTBOX_API_KEY_ID="postbox-api-key-id",
        COMMERCE_POSTBOX_API_KEY_SECRET="postbox-api-key-secret",
    )
    def test_deployed_enabled_worker_accepts_complete_postbox_configuration(self) -> None:
        self.assertEqual(run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG]), [])

    @override_settings(
        DEBUG=False,
        COMMERCE_WORKER_ENABLED=True,
        COMMERCE_EMAIL_SENDER_FACTORY="commerce.smtp_email_sender.smtp_email_sender_factory",
        COMMERCE_EMAIL_FROM_ADDRESS="orders@findme-photo.ru",
        COMMERCE_POSTBOX_API_KEY_ID="postbox-api-key-id",
        COMMERCE_POSTBOX_API_KEY_SECRET="postbox-api-key-secret",
    )
    def test_deployed_enabled_worker_requires_exact_postbox_factory(self) -> None:
        errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

        self.assertEqual([error.id for error in errors], ["commerce.E004"])

    @override_settings(
        DEBUG=False,
        COMMERCE_WORKER_ENABLED=True,
        COMMERCE_EMAIL_SENDER_FACTORY=(
            "commerce.postbox_email_sender.postbox_email_sender_factory"
        ),
        COMMERCE_EMAIL_FROM_ADDRESS="orders@findme-photo.ru",
        COMMERCE_POSTBOX_API_KEY_ID="postbox-api-key-id",
        COMMERCE_POSTBOX_API_KEY_SECRET="postbox-api-key-secret",
    )
    def test_deployed_enabled_worker_requires_non_local_valid_sender_identity(self) -> None:
        for sender_address in ("", "not-an-email", "noreply@localhost"):
            with self.subTest(sender_address=sender_address):
                with override_settings(COMMERCE_EMAIL_FROM_ADDRESS=sender_address):
                    errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

                self.assertEqual([error.id for error in errors], ["commerce.E005"])

    @override_settings(
        DEBUG=False,
        COMMERCE_WORKER_ENABLED=True,
        COMMERCE_EMAIL_SENDER_FACTORY=(
            "commerce.postbox_email_sender.postbox_email_sender_factory"
        ),
        COMMERCE_EMAIL_FROM_ADDRESS="orders@findme-photo.ru",
        COMMERCE_POSTBOX_API_KEY_ID="",
        COMMERCE_POSTBOX_API_KEY_SECRET="postbox-api-key-secret",
    )
    def test_deployed_enabled_worker_requires_postbox_api_key_id(self) -> None:
        errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

        self.assertEqual([error.id for error in errors], ["commerce.E006"])

    @override_settings(
        DEBUG=False,
        COMMERCE_WORKER_ENABLED=True,
        COMMERCE_EMAIL_SENDER_FACTORY=(
            "commerce.postbox_email_sender.postbox_email_sender_factory"
        ),
        COMMERCE_EMAIL_FROM_ADDRESS="orders@findme-photo.ru",
        COMMERCE_POSTBOX_API_KEY_ID="postbox-api-key-id",
        COMMERCE_POSTBOX_API_KEY_SECRET="",
    )
    def test_deployed_enabled_worker_requires_postbox_api_key_secret(self) -> None:
        errors = run_checks(tags=[COMMERCE_RUNTIME_CHECK_TAG])

        self.assertEqual([error.id for error in errors], ["commerce.E007"])
