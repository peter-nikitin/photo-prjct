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
