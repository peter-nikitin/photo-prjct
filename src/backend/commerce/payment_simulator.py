import hashlib
from collections.abc import Callable, Mapping
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from commerce.models import PaymentAttempt
from commerce.payment_gateway import (
    CreatedPayment,
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentObservation,
    PaymentRequest,
)

PAYMENT_SIMULATOR_ADAPTER_KEY = "feature-payment-simulator-v1"


class PaymentSimulatorGateway:
    """A feature-gated hosted-payment simulator backed by Commerce attempt evidence."""

    adapter_key = PAYMENT_SIMULATOR_ADAPTER_KEY

    def __init__(self, *, confirmation_url_for_payment: Callable[[str], str]) -> None:
        self._confirmation_url_for_payment = confirmation_url_for_payment

    def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        if not isinstance(request, PaymentRequest):
            raise PaymentGatewayError(PaymentGatewayErrorCategory.INVALID_RESPONSE)
        provider_payment_id = self._provider_payment_id(request.idempotency_key)
        return CreatedPayment(
            provider_payment_id=provider_payment_id,
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=request.amount_kopecks,
            currency=request.currency,
            confirmation_url=self._confirmation_url_for_payment(provider_payment_id),
            expires_at=timezone.now() + timedelta(hours=24),
        )

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        attempt = (
            PaymentAttempt.objects.filter(
                adapter_key=self.adapter_key,
                provider_payment_id=provider_payment_id,
            )
            .only(
                "provider_payment_id",
                "status",
                "amount_kopecks",
                "currency",
                "idempotency_key",
            )
            .first()
        )
        if attempt is None:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.NOT_FOUND)
        return PaymentObservation(
            provider_payment_id=attempt.provider_payment_id,
            status=_normalized_attempt_status(attempt.status),
            amount_kopecks=attempt.amount_kopecks,
            currency=attempt.currency,
            idempotency_key=attempt.idempotency_key,
        )

    def authenticate_notification(
        self,
        notification: IncomingPaymentNotification,
    ) -> PaymentObservation:
        del notification
        raise PaymentGatewayError(PaymentGatewayErrorCategory.AUTHENTICATION_FAILED)

    @staticmethod
    def _provider_payment_id(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        return f"simulated-payment-{digest}"


def payment_simulator_gateway_factory() -> PaymentSimulatorGateway:
    origin = str(getattr(settings, "COMMERCE_PUBLIC_ORIGIN", "")).rstrip("/")
    if not origin:
        raise ValueError("Commerce public origin is required.")
    return PaymentSimulatorGateway(
        confirmation_url_for_payment=lambda provider_payment_id: (
            origin
            + reverse(
                "commerce:payment_simulator",
                kwargs={"provider_payment_id": provider_payment_id},
            )
        )
    )


def simulator_observation(
    *,
    attempt: PaymentAttempt,
    outcome: str,
    provider_event_id: str,
) -> PaymentObservation:
    statuses: Mapping[str, NormalizedPaymentStatus] = {
        "pending": NormalizedPaymentStatus.PENDING,
        "succeeded": NormalizedPaymentStatus.SUCCEEDED,
        "canceled": NormalizedPaymentStatus.CANCELED,
    }
    try:
        status = statuses[outcome]
    except KeyError:
        raise ValueError("Unsupported simulated payment outcome.") from None
    return PaymentObservation(
        provider_payment_id=attempt.provider_payment_id,
        status=status,
        amount_kopecks=attempt.amount_kopecks,
        currency=attempt.currency,
        idempotency_key=attempt.idempotency_key,
        provider_event_id=provider_event_id,
    )


def _normalized_attempt_status(status: str) -> NormalizedPaymentStatus:
    statuses: Mapping[str, NormalizedPaymentStatus] = {
        "pending": NormalizedPaymentStatus.PENDING,
        "succeeded": NormalizedPaymentStatus.SUCCEEDED,
        "canceled": NormalizedPaymentStatus.CANCELED,
        "expired": NormalizedPaymentStatus.EXPIRED,
        "failed": NormalizedPaymentStatus.FAILED,
        "conflict": NormalizedPaymentStatus.FAILED,
    }
    return statuses[status]
