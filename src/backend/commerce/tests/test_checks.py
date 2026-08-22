from django.conf import settings
from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from commerce.checks import COMMERCE_RUNTIME_CHECK_TAG


class CommerceRuntimeSettingsTests(SimpleTestCase):
    def test_dark_defaults_keep_every_real_adapter_and_secret_blank(self) -> None:
        self.assertEqual(settings.COMMERCE_PAYMENT_GATEWAY_FACTORY, "")
        self.assertEqual(settings.COMMERCE_EMAIL_SENDER_FACTORY, "")
        self.assertEqual(settings.COMMERCE_WORKER_FACTORY, "")
        self.assertEqual(settings.COMMERCE_ORDER_ACCESS_SIGNING_SECRET, "")
        self.assertEqual(settings.COMMERCE_SUPPORT_CONTACT, "")
        self.assertEqual(settings.COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS, 300)
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
