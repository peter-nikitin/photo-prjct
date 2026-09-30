from collections.abc import Callable
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from commerce.capabilities import purchase_browser_authorizes_order
from commerce.checkout import (
    CheckoutPaymentUnavailable,
    _create_attempt,
    _is_safe_hosted_confirmation_url,
    _payment_request,
    initiate_payment,
)
from commerce.models import Order, PaymentAttempt
from commerce.payment_gateway import PaymentGateway


class OrderPaymentRejected(Exception):
    pass


def payment_continuation_state(order: Order) -> str | None:
    if order.status != Order.Status.PENDING:
        return None
    attempts = list(order.payment_attempts.all())
    if any(a.status == PaymentAttempt.Status.CONFLICT for a in attempts):
        return "support"
    active = next((a for a in attempts if a.status == PaymentAttempt.Status.PENDING), None)
    if active:
        return "hosted" if _is_safe_hosted_confirmation_url(active.confirmation_url) else "waiting"
    if any(a.status == PaymentAttempt.Status.SUCCEEDED for a in attempts):
        return "waiting"
    return "retry"


@sensitive_variables("purchase_browser_token")
def continue_order_payment(
    *,
    public_number: str,
    purchase_browser_token: object,
    gateway_factory: Callable[[], PaymentGateway],
    return_url_for_order: Callable[[str], str],
    now: datetime | None = None,
) -> str | None:
    current_time = now or timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(public_number=public_number).first()
        if (
            order is None
            or order.status != Order.Status.PENDING
            or not purchase_browser_authorizes_order(
                order=order,
                token=purchase_browser_token,
                now=current_time,
            )
        ):
            raise OrderPaymentRejected()
        attempts = list(PaymentAttempt.objects.select_for_update().filter(order=order))
        if any(a.status == PaymentAttempt.Status.CONFLICT for a in attempts):
            return None
        active = next((a for a in attempts if a.status == PaymentAttempt.Status.PENDING), None)
        if active:
            return (
                active.confirmation_url
                if _is_safe_hosted_confirmation_url(active.confirmation_url)
                else None
            )
        if any(a.status == PaymentAttempt.Status.SUCCEEDED for a in attempts):
            return None
        gateway = gateway_factory()
        adapter_key = gateway.adapter_key
        if not isinstance(adapter_key, str) or not adapter_key or len(adapter_key) > 64:
            raise CheckoutPaymentUnavailable()
        attempt = _create_attempt(order=order, adapter_key=adapter_key)
        payment_request = _payment_request(
            order=order, attempt=attempt, return_url_for_order=return_url_for_order
        )
    try:
        initiated = initiate_payment(
            attempt_id=attempt.pk, request=payment_request, gateway=gateway, now=current_time
        )
    except CheckoutPaymentUnavailable:
        return None
    return initiated.confirmation_url
