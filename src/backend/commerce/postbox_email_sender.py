import smtplib
import ssl
from email.message import EmailMessage as SmtpMessage

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from commerce.email_sender import EmailMessage, EmailSender, EmailSendOutcome, EmailSendResult

POSTBOX_SMTP_HOST = "postbox.cloud.yandex.net"
POSTBOX_SMTP_PORT = 465


class PostboxEmailSender:
    """Send provider-neutral plaintext messages through Yandex Cloud Postbox."""

    def __init__(
        self,
        *,
        from_address: str,
        api_key_id: str,
        api_key_secret: str,
    ) -> None:
        try:
            validate_email(from_address)
            if from_address.casefold().endswith("@localhost"):
                raise ValidationError("Local sender identities are not deployable.")
        except (TypeError, ValidationError):
            raise ValueError("Postbox configuration is invalid.") from None
        if (
            not isinstance(api_key_id, str)
            or not api_key_id.strip()
            or not api_key_id.isascii()
            or not isinstance(api_key_secret, str)
            or not api_key_secret.strip()
            or not api_key_secret.isascii()
        ):
            raise ValueError("Postbox configuration is invalid.")
        self._from_address = from_address
        self._api_key_id = api_key_id
        self._api_key_secret = api_key_secret

    def __repr__(self) -> str:
        return "PostboxEmailSender()"

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        if not isinstance(message, EmailMessage):
            raise TypeError("Postbox sender accepts provider-neutral messages only.")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError("Postbox timeout must be a positive number of seconds.")

        try:
            smtp_message = self._build_smtp_message(message)
        except (TypeError, ValueError, UnicodeError):
            return EmailSendResult(
                outcome=EmailSendOutcome.TERMINAL_FAILURE,
                safe_failure_category="invalid_message",
            )

        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        client: smtplib.SMTP_SSL | None = None
        try:
            client = smtplib.SMTP_SSL(
                POSTBOX_SMTP_HOST,
                POSTBOX_SMTP_PORT,
                timeout=timeout_seconds,
                context=context,
            )
            try:
                client.login(self._api_key_id, self._api_key_secret)
            except UnicodeError:
                return EmailSendResult(
                    outcome=EmailSendOutcome.TERMINAL_FAILURE,
                    safe_failure_category="smtp_configuration_error",
                )
            refused_recipients = client.send_message(smtp_message)
        except smtplib.SMTPAuthenticationError:
            return EmailSendResult(
                outcome=EmailSendOutcome.TERMINAL_FAILURE,
                safe_failure_category="smtp_authentication_failed",
            )
        except smtplib.SMTPRecipientsRefused as error:
            return self._recipient_failure(error.recipients)
        except smtplib.SMTPDataError as error:
            return self._response_failure(
                error.smtp_code,
                terminal_category="smtp_message_rejected",
            )
        except smtplib.SMTPResponseException as error:
            return self._response_failure(
                error.smtp_code,
                terminal_category="smtp_permanent_failure",
            )
        except smtplib.SMTPServerDisconnected:
            return EmailSendResult(
                outcome=EmailSendOutcome.RETRYABLE_FAILURE,
                safe_failure_category="smtp_unavailable",
            )
        except smtplib.SMTPException:
            return EmailSendResult(
                outcome=EmailSendOutcome.TERMINAL_FAILURE,
                safe_failure_category="smtp_configuration_error",
            )
        except (TimeoutError, OSError):
            return EmailSendResult(
                outcome=EmailSendOutcome.RETRYABLE_FAILURE,
                safe_failure_category="smtp_unavailable",
            )
        finally:
            self._close_safely(client)

        if refused_recipients:
            return self._recipient_failure(refused_recipients)
        return EmailSendResult(outcome=EmailSendOutcome.SUCCEEDED)

    @staticmethod
    def _close_safely(client: smtplib.SMTP_SSL | None) -> None:
        if client is None:
            return
        try:
            client.quit()
        except OSError:
            try:
                client.close()
            except OSError:
                pass

    def _build_smtp_message(self, message: EmailMessage) -> SmtpMessage:
        smtp_message = SmtpMessage()
        smtp_message["From"] = f"FindMe Photo <{self._from_address}>"
        smtp_message["To"] = message.recipient_email
        smtp_message["Subject"] = message.subject
        smtp_message.set_content(message.text_body)
        return smtp_message

    @staticmethod
    def _response_failure(
        smtp_code: int,
        *,
        terminal_category: str,
    ) -> EmailSendResult:
        if 400 <= smtp_code <= 499:
            return EmailSendResult(
                outcome=EmailSendOutcome.RETRYABLE_FAILURE,
                safe_failure_category="smtp_temporary_failure",
            )
        return EmailSendResult(
            outcome=EmailSendOutcome.TERMINAL_FAILURE,
            safe_failure_category=terminal_category,
        )

    @classmethod
    def _recipient_failure(
        cls,
        refused_recipients: dict[str, tuple[int, bytes]],
    ) -> EmailSendResult:
        if refused_recipients and all(
            400 <= response[0] <= 499 for response in refused_recipients.values()
        ):
            return cls._response_failure(
                400,
                terminal_category="smtp_recipient_rejected",
            )
        return EmailSendResult(
            outcome=EmailSendOutcome.TERMINAL_FAILURE,
            safe_failure_category="smtp_recipient_rejected",
        )


def postbox_email_sender_factory() -> EmailSender:
    return PostboxEmailSender(
        from_address=getattr(settings, "COMMERCE_EMAIL_FROM_ADDRESS", ""),
        api_key_id=getattr(settings, "COMMERCE_POSTBOX_API_KEY_ID", ""),
        api_key_secret=getattr(settings, "COMMERCE_POSTBOX_API_KEY_SECRET", ""),
    )
