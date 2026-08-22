import smtplib
from email.message import EmailMessage as SmtpMessage

from django.conf import settings

from commerce.email_sender import EmailMessage, EmailSender, EmailSendOutcome, EmailSendResult


class SmtpEmailSender:
    """Send the provider-neutral plaintext message through one configured SMTP relay."""

    def __init__(self, *, host: str, port: int) -> None:
        if (
            not host
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise ValueError("A valid SMTP endpoint is required.")
        self._host = host
        self._port = port

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        if not isinstance(message, EmailMessage):
            raise TypeError("SMTP sender accepts provider-neutral messages only.")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError("SMTP timeout must be a positive number of seconds.")
        smtp_message = SmtpMessage()
        smtp_message["From"] = "FindMe Photo <noreply@localhost>"
        smtp_message["To"] = message.recipient_email
        smtp_message["Subject"] = message.subject
        smtp_message.set_content(message.text_body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=timeout_seconds) as client:
                client.send_message(smtp_message)
        except (OSError, smtplib.SMTPException):
            return EmailSendResult(
                outcome=EmailSendOutcome.RETRYABLE_FAILURE,
                safe_failure_category="smtp_unavailable",
            )
        return EmailSendResult(outcome=EmailSendOutcome.SUCCEEDED)


def smtp_email_sender_factory() -> EmailSender:
    return SmtpEmailSender(
        host=str(getattr(settings, "COMMERCE_SMTP_HOST", "")),
        port=getattr(settings, "COMMERCE_SMTP_PORT", 25),
    )
