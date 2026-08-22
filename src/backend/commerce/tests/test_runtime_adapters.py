from email.message import EmailMessage as SmtpMessage
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from commerce.email_sender import EmailMessage, EmailSendOutcome


class SmtpEmailSenderTests(SimpleTestCase):
    @override_settings(COMMERCE_SMTP_HOST="mailpit", COMMERCE_SMTP_PORT=1025)
    def test_sender_delivers_plaintext_message_with_bounded_timeout(self) -> None:
        from commerce.smtp_email_sender import smtp_email_sender_factory

        with patch("commerce.smtp_email_sender.smtplib.SMTP") as smtp_class:
            result = smtp_email_sender_factory().send(
                EmailMessage(
                    recipient_email="buyer@example.test",
                    subject="Доступ к заказу",
                    text_body="Откройте ссылку:\nhttps://example.test/access",
                ),
                timeout_seconds=17,
            )

        self.assertEqual(result.outcome, EmailSendOutcome.SUCCEEDED)
        smtp_class.assert_called_once_with("mailpit", 1025, timeout=17)
        sent = smtp_class.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertIsInstance(sent, SmtpMessage)
        self.assertEqual(sent["To"], "buyer@example.test")
        self.assertEqual(sent["Subject"], "Доступ к заказу")
        self.assertEqual(
            sent.get_content().strip(), "Откройте ссылку:\nhttps://example.test/access"
        )


class CommerceRuntimeFactoryTests(SimpleTestCase):
    @override_settings(
        DEBUG=True,
        COMMERCE_PUBLIC_ORIGIN="http://127.0.0.1:8000",
        COMMERCE_ORDER_ACCESS_SIGNING_SECRET="local-signing-secret",
        COMMERCE_SUPPORT_CONTACT="support@example.test",
        COMMERCE_PAYMENT_GATEWAY_FACTORY=(
            "commerce.payment_simulator.payment_simulator_gateway_factory"
        ),
        COMMERCE_EMAIL_SENDER_FACTORY="commerce.smtp_email_sender.smtp_email_sender_factory",
        COMMERCE_SMTP_HOST="mailpit",
        COMMERCE_SMTP_PORT=1025,
    )
    def test_worker_factory_builds_complete_local_runtime(self) -> None:
        from commerce.runtime import commerce_worker_factory
        from commerce.worker import CommerceWorker

        worker = commerce_worker_factory()

        self.assertIsInstance(worker, CommerceWorker)

    @override_settings(
        DEBUG=False,
        COMMERCE_PUBLIC_ORIGIN="http://127.0.0.1:8000",
        COMMERCE_ORDER_ACCESS_SIGNING_SECRET="local-signing-secret",
        COMMERCE_SUPPORT_CONTACT="support@example.test",
        COMMERCE_PAYMENT_GATEWAY_FACTORY=(
            "commerce.payment_simulator.payment_simulator_gateway_factory"
        ),
        COMMERCE_EMAIL_SENDER_FACTORY="commerce.smtp_email_sender.smtp_email_sender_factory",
    )
    def test_worker_factory_rejects_plain_http_outside_debug(self) -> None:
        from commerce.runtime import commerce_worker_factory

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            commerce_worker_factory()
