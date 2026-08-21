from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class NormalizedPaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    EXPIRED = "expired"
    FAILED = "failed"


class PaymentGatewayErrorCategory(StrEnum):
    AUTHENTICATION_FAILED = "authentication_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVALID_RESPONSE = "invalid_response"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class PaymentGatewayError(Exception):
    """A provider-neutral failure safe for application logs and customer errors."""

    def __init__(self, category: PaymentGatewayErrorCategory) -> None:
        self.category = category
        super().__init__(category.value)


def _positive_kopecks(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer kopeck amount.")
    return value


@dataclass(frozen=True)
class PaymentReceiptLine:
    description: str
    quantity: int
    unit_amount_kopecks: int
    line_total_kopecks: int

    def __post_init__(self) -> None:
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Payment line description is required.")
        if self.quantity != 1:
            raise ValueError("Payment line quantity must be one.")
        unit_amount = _positive_kopecks(
            self.unit_amount_kopecks,
            field_name="Payment line unit amount",
        )
        line_total = _positive_kopecks(
            self.line_total_kopecks,
            field_name="Payment line total",
        )
        if line_total != unit_amount:
            raise ValueError("Payment line total must equal its unit amount.")


@dataclass(frozen=True)
class PaymentRequest:
    order_public_number: str
    amount_kopecks: int
    currency: str
    receipt_lines: tuple[PaymentReceiptLine, ...]
    checkout_email: str
    idempotency_key: str
    return_url: str

    def __post_init__(self) -> None:
        amount = _positive_kopecks(self.amount_kopecks, field_name="Payment amount")
        if self.currency != "RUB":
            raise ValueError("Payment currency must be RUB.")
        if not isinstance(self.receipt_lines, tuple) or not self.receipt_lines:
            raise ValueError("Payment request requires receipt lines.")
        if not all(isinstance(line, PaymentReceiptLine) for line in self.receipt_lines):
            raise TypeError("Payment receipt lines must use provider-neutral DTOs.")
        if sum(line.line_total_kopecks for line in self.receipt_lines) != amount:
            raise ValueError("Payment receipt lines must equal the exact payment amount.")
        if not isinstance(self.checkout_email, str) or not self.checkout_email:
            raise ValueError("Checkout email is required.")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ValueError("Application idempotency key is required.")
        if len(self.idempotency_key) > 128:
            raise ValueError("Application idempotency key is too long.")
        if (
            not isinstance(self.order_public_number, str)
            or not self.order_public_number
            or not isinstance(self.return_url, str)
            or self.order_public_number not in self.return_url
        ):
            raise ValueError("Return URL must contain the public Order reference.")


def _validate_normalized_payment(
    *,
    provider_payment_id: object,
    status: object,
    amount_kopecks: object,
    currency: object,
) -> None:
    if not isinstance(provider_payment_id, str) or not provider_payment_id:
        raise ValueError("Provider payment identifier is required.")
    if not isinstance(status, NormalizedPaymentStatus):
        raise TypeError("Payment status must be normalized by the adapter.")
    _positive_kopecks(amount_kopecks, field_name="Observed payment amount")
    if currency != "RUB":
        raise ValueError("Observed payment currency must be RUB.")


@dataclass(frozen=True)
class CreatedPayment:
    provider_payment_id: str
    status: NormalizedPaymentStatus
    amount_kopecks: int
    currency: str
    confirmation_url: str
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _validate_normalized_payment(
            provider_payment_id=self.provider_payment_id,
            status=self.status,
            amount_kopecks=self.amount_kopecks,
            currency=self.currency,
        )
        if not isinstance(self.confirmation_url, str) or not self.confirmation_url:
            raise ValueError("Hosted confirmation URL is required.")
        if self.expires_at is not None and not isinstance(self.expires_at, datetime):
            raise TypeError("Provider expiry must be a datetime or absent.")


@dataclass(frozen=True)
class PaymentObservation:
    provider_payment_id: str
    status: NormalizedPaymentStatus
    amount_kopecks: int
    currency: str
    idempotency_key: str
    provider_event_id: str = ""

    def __post_init__(self) -> None:
        _validate_normalized_payment(
            provider_payment_id=self.provider_payment_id,
            status=self.status,
            amount_kopecks=self.amount_kopecks,
            currency=self.currency,
        )
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ValueError("Application idempotency key is required.")
        if not isinstance(self.provider_event_id, str):
            raise TypeError("Provider event identifier must be normalized text.")


@dataclass(frozen=True)
class IncomingPaymentNotification:
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.headers, Mapping):
            raise TypeError("Notification headers must be a mapping.")
        if not isinstance(self.body, bytes):
            raise TypeError("Notification body must be bytes.")


class PaymentGateway(Protocol):
    adapter_key: str

    def create_payment(self, request: PaymentRequest) -> CreatedPayment: ...

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation: ...

    def authenticate_notification(
        self,
        notification: IncomingPaymentNotification,
    ) -> PaymentObservation: ...
