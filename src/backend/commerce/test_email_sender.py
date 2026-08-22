from commerce.email_sender import EmailMessage, EmailSendOutcome, EmailSendResult


class DeterministicEmailSender:
    """A local/test adapter that records messages and never performs network I/O."""

    def __init__(self, *, outcomes: tuple[EmailSendOutcome, ...] = ()) -> None:
        if any(not isinstance(outcome, EmailSendOutcome) for outcome in outcomes):
            raise TypeError("Deterministic email outcomes must be normalized.")
        self._outcomes = list(outcomes)
        self.captured_messages: list[EmailMessage] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        if not isinstance(message, EmailMessage):
            raise TypeError("Email senders accept provider-neutral messages only.")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError("Email sender timeout must be a positive number of seconds.")
        self.captured_messages.append(message)
        outcome = self._outcomes.pop(0) if self._outcomes else EmailSendOutcome.SUCCEEDED
        if outcome == EmailSendOutcome.SUCCEEDED:
            return EmailSendResult(outcome=outcome)
        return EmailSendResult(
            outcome=outcome,
            safe_failure_category=f"deterministic_{outcome.value}",
        )
