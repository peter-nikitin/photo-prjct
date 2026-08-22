from dataclasses import dataclass, field
from enum import StrEnum
from re import fullmatch
from typing import Protocol


class EmailSendOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True)
class EmailMessage:
    """One complete provider-neutral plaintext message without tracking or attachments."""

    recipient_email: str = field(repr=False)
    subject: str
    text_body: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.recipient_email, str) or not self.recipient_email:
            raise ValueError("An email recipient is required.")
        if not isinstance(self.subject, str) or not self.subject:
            raise ValueError("An email subject is required.")
        if not isinstance(self.text_body, str) or not self.text_body:
            raise ValueError("An email text body is required.")


@dataclass(frozen=True)
class EmailSendResult:
    outcome: EmailSendOutcome
    safe_failure_category: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, EmailSendOutcome):
            raise TypeError("Email delivery outcomes must be normalized by the adapter.")
        if not isinstance(self.safe_failure_category, str):
            raise TypeError("Email failure category must be safe text.")
        if self.outcome == EmailSendOutcome.SUCCEEDED and self.safe_failure_category:
            raise ValueError("Successful email delivery has no failure category.")
        if (
            self.outcome != EmailSendOutcome.SUCCEEDED
            and fullmatch(r"[a-z0-9_]{1,64}", self.safe_failure_category) is None
        ):
            raise ValueError("Failed email delivery requires a safe failure category.")


class EmailSender(Protocol):
    """The only Commerce boundary for customer and operator email delivery."""

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult: ...
