import hashlib
import hmac
import json
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock

from django.utils import timezone

from commerce.payment_gateway import (
    CreatedPayment,
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentObservation,
    PaymentRequest,
)


class TestPaymentOutcome(StrEnum):
    SUCCESS = "Успех"
    CANCEL = "Отмена"
    PENDING = "Оставить в ожидании"


TestPaymentOutcome.__test__ = False  # type: ignore[attr-defined]


class DeterministicPaymentGateway:
    """An in-memory local/test adapter with no network or provider dependency."""

    def __init__(
        self,
        *,
        outcome: TestPaymentOutcome,
        notification_secret: bytes,
        now: datetime | None = None,
        adapter_key: str = "deterministic-test",
    ) -> None:
        if not isinstance(outcome, TestPaymentOutcome):
            raise ValueError("A deterministic payment outcome is required.")
        if not isinstance(notification_secret, bytes) or not notification_secret:
            raise ValueError("A test notification secret is required.")
        if not isinstance(adapter_key, str) or not adapter_key or len(adapter_key) > 64:
            raise ValueError("A bounded adapter key is required.")
        self.adapter_key = adapter_key
        self._outcome = outcome
        self._notification_secret = notification_secret
        self._now = now
        self._lock = Lock()
        self._requests: dict[str, PaymentRequest] = {}
        self._payments: dict[str, tuple[PaymentRequest, CreatedPayment]] = {}

    def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        if not isinstance(request, PaymentRequest):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        with self._lock:
            existing_request = self._requests.get(request.idempotency_key)
            if existing_request is not None and existing_request != request:
                raise PaymentGatewayError(PaymentGatewayErrorCategory.IDEMPOTENCY_CONFLICT)
            if existing_request is not None:
                provider_id = self._provider_payment_id(request.idempotency_key)
                return self._payments[provider_id][1]

            provider_id = self._provider_payment_id(request.idempotency_key)
            created = CreatedPayment(
                provider_payment_id=provider_id,
                status=self._normalized_status(),
                amount_kopecks=request.amount_kopecks,
                currency=request.currency,
                confirmation_url=f"https://payment.test.invalid/{provider_id}",
                expires_at=(self._now or timezone.now()) + timedelta(hours=24),
            )
            self._requests[request.idempotency_key] = request
            self._payments[provider_id] = (request, created)
            return created

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        with self._lock:
            stored = self._payments.get(provider_payment_id)
        if stored is None:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.NOT_FOUND)
        request, created = stored
        return PaymentObservation(
            provider_payment_id=created.provider_payment_id,
            status=created.status,
            amount_kopecks=created.amount_kopecks,
            currency=created.currency,
            idempotency_key=request.idempotency_key,
        )

    def authenticate_notification(
        self,
        notification: IncomingPaymentNotification,
    ) -> PaymentObservation:
        if not isinstance(notification, IncomingPaymentNotification):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.AUTHENTICATION_FAILED)
        signature = next(
            (
                value
                for name, value in notification.headers.items()
                if name.casefold() == "x-test-payment-signature"
            ),
            "",
        )
        expected = hmac.new(
            self._notification_secret,
            notification.body,
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.AUTHENTICATION_FAILED)
        try:
            payload = json.loads(notification.body)
            provider_payment_id = payload["provider_payment_id"]
            provider_event_id = payload["provider_event_id"]
            status = NormalizedPaymentStatus(payload["status"])
            amount_kopecks = payload["amount_kopecks"]
            currency = payload["currency"]
            fetched = self.fetch_payment(provider_payment_id)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, PaymentGatewayError):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE) from None
        if (
            not isinstance(provider_event_id, str)
            or not provider_event_id
            or status != fetched.status
            or amount_kopecks != fetched.amount_kopecks
            or currency != fetched.currency
        ):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        return PaymentObservation(
            provider_payment_id=fetched.provider_payment_id,
            status=status,
            amount_kopecks=amount_kopecks,
            currency=currency,
            idempotency_key=fetched.idempotency_key,
            provider_event_id=provider_event_id,
        )

    def _normalized_status(self) -> NormalizedPaymentStatus:
        return {
            TestPaymentOutcome.SUCCESS: NormalizedPaymentStatus.SUCCEEDED,
            TestPaymentOutcome.CANCEL: NormalizedPaymentStatus.CANCELED,
            TestPaymentOutcome.PENDING: NormalizedPaymentStatus.PENDING,
        }[self._outcome]

    @staticmethod
    def _provider_payment_id(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        return f"test-payment-{digest}"
