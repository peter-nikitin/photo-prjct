from django.test import SimpleTestCase

from commerce.email_sender import EmailMessage, EmailSendOutcome, EmailSendResult
from commerce.test_email_sender import DeterministicEmailSender


class DeterministicEmailSenderTests(SimpleTestCase):
    """The breaks caught here would hide a provider result or send an unreviewed payload."""

    def test_captures_one_provider_neutral_message_and_returns_configured_outcomes(self) -> None:
        """A sender that dropped the body or mislabeled a failure would corrupt delivery state."""
        message = EmailMessage(
            recipient_email="buyer@example.test",
            subject="Ваши фотографии с мероприятия «Night Ride»",
            text_body="Открыть оригиналы:\nhttps://findme.example.test/orders/access",
        )
        sender = DeterministicEmailSender(
            outcomes=(
                EmailSendOutcome.RETRYABLE_FAILURE,
                EmailSendOutcome.TERMINAL_FAILURE,
                EmailSendOutcome.SUCCEEDED,
            )
        )

        first = sender.send(message, timeout_seconds=15)
        second = sender.send(message, timeout_seconds=15)
        third = sender.send(message, timeout_seconds=15)

        self.assertEqual(
            [first.outcome, second.outcome, third.outcome],
            [
                EmailSendOutcome.RETRYABLE_FAILURE,
                EmailSendOutcome.TERMINAL_FAILURE,
                EmailSendOutcome.SUCCEEDED,
            ],
        )
        self.assertEqual(sender.captured_messages, [message, message, message])
        self.assertEqual(
            {field.name for field in EmailMessage.__dataclass_fields__.values()},
            {"recipient_email", "subject", "text_body"},
        )

    def test_rejects_a_provider_failure_category_that_could_carry_raw_or_customer_data(
        self,
    ) -> None:
        """Persisting provider text as a failure category could leak private delivery evidence."""
        with self.assertRaisesRegex(ValueError, "safe"):
            EmailSendResult(
                outcome=EmailSendOutcome.RETRYABLE_FAILURE,
                safe_failure_category="buyer@example.test: provider timeout",
            )
