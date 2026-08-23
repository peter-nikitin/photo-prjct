import smtplib
import ssl
from email.message import EmailMessage as SmtpMessage
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from commerce.email_sender import EmailMessage, EmailSendOutcome
from commerce.postbox_email_sender import PostboxEmailSender, postbox_email_sender_factory


class _FakeSmtp:
    def __init__(
        self,
        *,
        login_error: Exception | None = None,
        send_error: Exception | None = None,
        cleanup_error: Exception | None = None,
        refused_recipients: dict[str, tuple[int, bytes]] | None = None,
    ) -> None:
        self.login_error = login_error
        self.send_error = send_error
        self.cleanup_error = cleanup_error
        self.refused_recipients = refused_recipients or {}
        self.login_calls: list[tuple[str, str]] = []
        self.messages: list[SmtpMessage] = []
        self.cleanup_calls = 0

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        if self.cleanup_error is not None:
            raise self.cleanup_error

    def quit(self) -> None:
        self.cleanup_calls += 1
        if self.cleanup_error is not None:
            raise self.cleanup_error

    def close(self) -> None:
        pass

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))
        if self.login_error is not None:
            raise self.login_error

    def send_message(self, message: SmtpMessage) -> dict[str, tuple[int, bytes]]:
        self.messages.append(message)
        if self.send_error is not None:
            raise self.send_error
        return self.refused_recipients


class _SmtpFactory:
    def __init__(self, client: _FakeSmtp | Exception) -> None:
        self.client = client
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    def __call__(self, host: str, port: int, **kwargs: Any) -> _FakeSmtp:
        self.calls.append((host, port, kwargs))
        if isinstance(self.client, Exception):
            raise self.client
        return self.client


@override_settings(
    COMMERCE_EMAIL_FROM_ADDRESS="orders@findme-photo.ru",
    COMMERCE_POSTBOX_API_KEY_ID="postbox-api-key-id",
    COMMERCE_POSTBOX_API_KEY_SECRET="postbox-api-key-secret",
)
class PostboxEmailSenderTests(SimpleTestCase):
    message = EmailMessage(
        recipient_email="покупатель@example.test",
        subject="Ваши фотографии с мероприятия «Забег»",
        text_body="Открыть оригиналы:\nhttps://findme-photo.ru/orders/access/secret-link",
    )

    def test_submits_utf8_plaintext_over_authenticated_verified_smtps(self) -> None:
        """Wrong endpoint, identity, TLS policy, or MIME bytes would lose production email."""
        client = _FakeSmtp()
        smtp_factory = _SmtpFactory(client)

        with patch("commerce.postbox_email_sender.smtplib.SMTP_SSL", new=smtp_factory):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.SUCCEEDED)
        self.assertEqual(len(smtp_factory.calls), 1)
        host, port, kwargs = smtp_factory.calls[0]
        self.assertEqual((host, port), ("postbox.cloud.yandex.net", 465))
        self.assertEqual(kwargs["timeout"], 17)
        context = kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(
            client.login_calls,
            [("postbox-api-key-id", "postbox-api-key-secret")],
        )
        sent = client.messages[0]
        self.assertEqual(sent["From"], "FindMe Photo <orders@findme-photo.ru>")
        self.assertEqual(sent["To"], "покупатель@example.test")
        self.assertEqual(sent["Subject"], "Ваши фотографии с мероприятия «Забег»")
        self.assertEqual(sent.get_content().strip(), self.message.text_body)
        self.assertEqual(sent.get_content_charset(), "utf-8")

    def test_maps_smtp_4xx_response_to_retryable_failure(self) -> None:
        """A temporary provider rejection must remain eligible for the bounded retry schedule."""
        client = _FakeSmtp(send_error=smtplib.SMTPDataError(451, b"temporary provider text"))

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_temporary_failure")

    def test_cleanup_failure_cannot_downgrade_confirmed_provider_acceptance(self) -> None:
        """Retry after an accepted message would duplicate a customer's permanent access mail."""
        client = _FakeSmtp(
            cleanup_error=smtplib.SMTPResponseException(451, b"QUIT temporarily failed")
        )

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.SUCCEEDED)
        self.assertEqual(result.safe_failure_category, "")

    def test_maps_recipient_5xx_response_to_terminal_failure(self) -> None:
        """Retrying a permanently rejected recipient would waste every delivery attempt."""
        error = smtplib.SMTPRecipientsRefused({"buyer@example.test": (550, b"recipient rejected")})
        client = _FakeSmtp(send_error=error)

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.TERMINAL_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_recipient_rejected")

    def test_maps_data_5xx_response_to_terminal_failure(self) -> None:
        """A permanent provider content rejection must not enter a futile retry loop."""
        client = _FakeSmtp(send_error=smtplib.SMTPDataError(554, b"message rejected"))

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.TERMINAL_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_message_rejected")

    def test_maps_authentication_failure_to_safe_terminal_category(self) -> None:
        """Invalid credentials require operator repair, not customer delivery retries."""
        client = _FakeSmtp(login_error=smtplib.SMTPAuthenticationError(535, b"credential rejected"))

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.TERMINAL_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_authentication_failed")

    def test_maps_connection_and_disconnect_failures_to_retryable_category(self) -> None:
        """Transient network loss must not permanently abandon a paid Order's email."""
        failures = (
            TimeoutError("provider timeout"),
            OSError("network unavailable"),
            smtplib.SMTPServerDisconnected("connection closed"),
        )

        for failure in failures:
            with self.subTest(failure_type=type(failure).__name__):
                with patch(
                    "commerce.postbox_email_sender.smtplib.SMTP_SSL",
                    new=_SmtpFactory(failure),
                ):
                    result = postbox_email_sender_factory().send(
                        self.message,
                        timeout_seconds=17,
                    )

                self.assertEqual(result.outcome, EmailSendOutcome.RETRYABLE_FAILURE)
                self.assertEqual(result.safe_failure_category, "smtp_unavailable")

    def test_maps_non_response_smtp_protocol_failures_to_terminal_configuration_category(
        self,
    ) -> None:
        """Missing AUTH support and generic protocol failures require operator repair."""
        failures = (
            smtplib.SMTPException("protocol failure"),
            smtplib.SMTPNotSupportedError("AUTH is unavailable"),
        )

        for failure in failures:
            with self.subTest(failure_type=type(failure).__name__):
                client = _FakeSmtp(login_error=failure)
                with patch(
                    "commerce.postbox_email_sender.smtplib.SMTP_SSL",
                    new=_SmtpFactory(client),
                ):
                    result = postbox_email_sender_factory().send(
                        self.message,
                        timeout_seconds=17,
                    )

                self.assertEqual(result.outcome, EmailSendOutcome.TERMINAL_FAILURE)
                self.assertEqual(result.safe_failure_category, "smtp_configuration_error")

    def test_refused_result_without_exception_is_terminal(self) -> None:
        """A refused one-recipient transaction must never be recorded as provider acceptance."""
        client = _FakeSmtp(refused_recipients={"buyer@example.test": (550, b"recipient rejected")})

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.TERMINAL_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_recipient_rejected")

    def test_temporary_refusal_result_without_exception_is_retryable(self) -> None:
        """A returned recipient 451 must stay eligible for the bounded delivery retry schedule."""
        client = _FakeSmtp(
            refused_recipients={"buyer@example.test": (451, b"temporary recipient failure")}
        )

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        self.assertEqual(result.outcome, EmailSendOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_temporary_failure")

    def test_rejects_invalid_runtime_configuration_without_disclosing_values(self) -> None:
        """Incomplete or malformed credentials must fail before any SMTP connection is opened."""
        configurations = (
            {
                "COMMERCE_EMAIL_FROM_ADDRESS": "",
                "COMMERCE_POSTBOX_API_KEY_ID": "postbox-api-key-id",
                "COMMERCE_POSTBOX_API_KEY_SECRET": "postbox-api-key-secret",
            },
            {
                "COMMERCE_EMAIL_FROM_ADDRESS": "not-an-email",
                "COMMERCE_POSTBOX_API_KEY_ID": "postbox-api-key-id",
                "COMMERCE_POSTBOX_API_KEY_SECRET": "postbox-api-key-secret",
            },
            {
                "COMMERCE_EMAIL_FROM_ADDRESS": "noreply@localhost",
                "COMMERCE_POSTBOX_API_KEY_ID": "postbox-api-key-id",
                "COMMERCE_POSTBOX_API_KEY_SECRET": "postbox-api-key-secret",
            },
            {
                "COMMERCE_EMAIL_FROM_ADDRESS": "orders@findme-photo.ru",
                "COMMERCE_POSTBOX_API_KEY_ID": "",
                "COMMERCE_POSTBOX_API_KEY_SECRET": "postbox-api-key-secret",
            },
            {
                "COMMERCE_EMAIL_FROM_ADDRESS": "orders@findme-photo.ru",
                "COMMERCE_POSTBOX_API_KEY_ID": "postbox-api-key-id",
                "COMMERCE_POSTBOX_API_KEY_SECRET": "",
            },
        )

        for index, configured in enumerate(configurations):
            with self.subTest(configuration_index=index):
                with override_settings(**configured):
                    with self.assertRaisesRegex(
                        ValueError, "Postbox configuration is invalid"
                    ) as caught:
                        postbox_email_sender_factory()

                rendered = repr(caught.exception)
                self.assertNotIn("orders@findme-photo.ru", rendered)
                self.assertNotIn("postbox-api-key-id", rendered)
                self.assertNotIn("postbox-api-key-secret", rendered)

    def test_rejects_non_ascii_credentials_before_network_without_disclosing_them(self) -> None:
        """SMTP AUTH encoding errors must not retain credentials or reach a network connection."""
        configurations = (
            ("ключ-api-id", "postbox-api-key-secret"),
            ("postbox-api-key-id", "секрет-api-key"),
        )

        for api_key_id, api_key_secret in configurations:
            with self.subTest(non_ascii_value=(api_key_id, api_key_secret)):
                smtp_factory = _SmtpFactory(_FakeSmtp())
                with override_settings(
                    COMMERCE_POSTBOX_API_KEY_ID=api_key_id,
                    COMMERCE_POSTBOX_API_KEY_SECRET=api_key_secret,
                ):
                    with patch(
                        "commerce.postbox_email_sender.smtplib.SMTP_SSL",
                        new=smtp_factory,
                    ):
                        with self.assertRaisesRegex(
                            ValueError,
                            "Postbox configuration is invalid",
                        ) as caught:
                            postbox_email_sender_factory().send(
                                self.message,
                                timeout_seconds=17,
                            )

                self.assertEqual(smtp_factory.calls, [])
                rendered = repr(caught.exception)
                self.assertNotIn(api_key_id, rendered)
                self.assertNotIn(api_key_secret, rendered)

    def test_defensively_normalizes_login_unicode_error_without_disclosing_credentials(
        self,
    ) -> None:
        """Unexpected AUTH encoding failure must remain inside the secret-safe adapter boundary."""
        credential_text = "postbox-api-key-id:postbox-api-key-secret"
        login_error = UnicodeEncodeError(
            "ascii",
            credential_text,
            0,
            len(credential_text),
            "invalid AUTH text",
        )
        client = _FakeSmtp(login_error=login_error)

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = postbox_email_sender_factory().send(self.message, timeout_seconds=17)

        rendered = repr(result)
        self.assertEqual(result.outcome, EmailSendOutcome.TERMINAL_FAILURE)
        self.assertEqual(result.safe_failure_category, "smtp_configuration_error")
        self.assertNotIn("postbox-api-key-id", rendered)
        self.assertNotIn("postbox-api-key-secret", rendered)

    def test_sender_repr_and_provider_failure_result_never_disclose_message_or_credentials(
        self,
    ) -> None:
        """Diagnostic rendering must not expose personal data, grants, or provider credentials."""
        sender = PostboxEmailSender(
            from_address="orders@findme-photo.ru",
            api_key_id="postbox-api-key-id",
            api_key_secret="postbox-api-key-secret",
        )
        provider_text = (
            b"buyer@example.test postbox-api-key-id postbox-api-key-secret "
            b"https://findme-photo.ru/orders/access/secret-link"
        )
        client = _FakeSmtp(send_error=smtplib.SMTPDataError(554, provider_text))

        with patch(
            "commerce.postbox_email_sender.smtplib.SMTP_SSL",
            new=_SmtpFactory(client),
        ):
            result = sender.send(self.message, timeout_seconds=17)

        rendered = f"{sender!r} {result!r}"
        for secret in (
            "orders@findme-photo.ru",
            "buyer@example.test",
            "postbox-api-key-id",
            "postbox-api-key-secret",
            "secret-link",
            "Ваши фотографии",
        ):
            self.assertNotIn(secret, rendered)
