from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from commerce.attention import open_attention, resolve_open_attention_automatically
from commerce.capabilities import create_order_access_grant, sign_order_access_grant
from commerce.email_sender import EmailMessage, EmailSender, EmailSendOutcome, EmailSendResult
from commerce.models import EmailDelivery, EmailDeliveryAttempt, Order, OrderAccessGrant
from commerce.pricing import format_rub

_MAX_DELIVERY_CLAIM_LIMIT = 20
_DELIVERY_LEASE_DURATION = timedelta(minutes=5)
_RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
RESEND_MINIMUM_INTERVAL = timedelta(minutes=1)


class ResendOrderAccessRateLimited(Exception):
    pass


@dataclass(frozen=True)
class EmailDeliveryClaim:
    """A short-lived database lease identifier without recipient or bearer data."""

    delivery_id: int
    lease_id: UUID


def claim_due_email_deliveries(
    *,
    now: datetime | None = None,
    limit: int = _MAX_DELIVERY_CLAIM_LIMIT,
    lease_duration: timedelta = _DELIVERY_LEASE_DURATION,
) -> tuple[EmailDeliveryClaim, ...]:
    """Recover and claim only a bounded amount of ready delivery work."""
    current_time = _current_time(now)
    _validate_claim_request(limit=limit, lease_duration=lease_duration)
    with transaction.atomic():
        _recover_expired_delivery_leases(now=current_time, limit=limit)
        deliveries = list(
            EmailDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                state__in=(EmailDelivery.State.PENDING, EmailDelivery.State.RETRY_WAIT),
                next_attempt_at__lte=current_time,
            )
            .order_by("next_attempt_at", "pk")[:limit]
        )
        claims: list[EmailDeliveryClaim] = []
        for delivery in deliveries:
            lease_id = uuid4()
            delivery.state = EmailDelivery.State.PROCESSING
            delivery.lease_id = lease_id
            delivery.lease_expires_at = current_time + lease_duration
            delivery.save(update_fields=["state", "lease_id", "lease_expires_at", "updated_at"])
            claims.append(EmailDeliveryClaim(delivery_id=delivery.pk, lease_id=lease_id))
    return tuple(claims)


def send_claimed_email_delivery(
    *,
    claim: EmailDeliveryClaim,
    email_sender: EmailSender,
    order_access_signing_secret: str | bytes,
    order_access_url_for_grant: Callable[[OrderAccessGrant, str], str],
    support_contact: str,
    timeout_seconds: int,
    now: datetime | None = None,
) -> EmailDelivery | None:
    """Send one claimed email outside transactions, then append its durable result."""
    delivery = _delivery_for_active_claim(claim=claim, now=_current_time(now))
    if delivery is None:
        return None
    message = _customer_access_message(
        delivery=delivery,
        order_access_signing_secret=order_access_signing_secret,
        order_access_url_for_grant=order_access_url_for_grant,
        support_contact=support_contact,
    )
    try:
        result = email_sender.send(message, timeout_seconds=timeout_seconds)
    except TimeoutError:
        result = EmailSendResult(
            outcome=EmailSendOutcome.RETRYABLE_FAILURE,
            safe_failure_category="timeout",
        )
    if not isinstance(result, EmailSendResult):
        raise TypeError("Email senders must return normalized delivery results.")
    return _record_delivery_result(claim=claim, result=result, now=_current_time(now))


def correct_delivery_email(
    *,
    order_id: int,
    delivery_email: str,
    now: datetime | None = None,
) -> Order:
    """Cancel unsent obsolete delivery work without altering checkout or success history."""
    normalized_email = _normalized_delivery_email(delivery_email)
    current_time = _current_time(now)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.delivery_email == normalized_email:
            return order
        order.delivery_email = normalized_email
        order.save(update_fields=["delivery_email"])
        unsent = EmailDelivery.objects.select_for_update().filter(
            order=order,
            state__in=(
                EmailDelivery.State.PENDING,
                EmailDelivery.State.RETRY_WAIT,
                EmailDelivery.State.FAILED,
            ),
        )
        for delivery in unsent:
            delivery.state = EmailDelivery.State.CANCELED
            delivery.lease_id = None
            delivery.lease_expires_at = None
            delivery.next_attempt_at = current_time
            delivery.save(
                update_fields=[
                    "state",
                    "lease_id",
                    "lease_expires_at",
                    "next_attempt_at",
                    "updated_at",
                ]
            )
    return order


def resend_order_access(*, order_id: int, now: datetime | None = None) -> EmailDelivery:
    """Create a new independently revocable grant and delivery for the current recipient."""
    current_time = _current_time(now)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.PAID:
            raise ValueError("Only paid Orders may receive access email again.")
        if EmailDelivery.objects.filter(
            order=order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            access_grant__source=OrderAccessGrant.Source.RESEND,
            created_at__gte=current_time - RESEND_MINIMUM_INTERVAL,
        ).exists():
            raise ResendOrderAccessRateLimited()
        grant = create_order_access_grant(order=order, source=str(OrderAccessGrant.Source.RESEND))
        return EmailDelivery.objects.create(
            order=order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=order.delivery_email,
            access_grant=grant,
            next_attempt_at=current_time,
        )


def _recover_expired_delivery_leases(*, now: datetime, limit: int) -> None:
    expired = list(
        EmailDelivery.objects.select_for_update(skip_locked=True)
        .filter(
            state=EmailDelivery.State.PROCESSING,
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at", "pk")[:limit]
    )
    for delivery in expired:
        delivery.state = EmailDelivery.State.RETRY_WAIT
        delivery.lease_id = None
        delivery.lease_expires_at = None
        delivery.next_attempt_at = now
        delivery.last_failure_category = "lease_expired"
        delivery.save(
            update_fields=[
                "state",
                "lease_id",
                "lease_expires_at",
                "next_attempt_at",
                "last_failure_category",
                "updated_at",
            ]
        )


def _delivery_for_active_claim(*, claim: EmailDeliveryClaim, now: datetime) -> EmailDelivery | None:
    if not isinstance(claim, EmailDeliveryClaim):
        raise TypeError("Email delivery claims must come from the Commerce worker.")
    delivery = (
        EmailDelivery.objects.select_related("order", "access_grant")
        .filter(
            pk=claim.delivery_id,
            state=EmailDelivery.State.PROCESSING,
            lease_id=claim.lease_id,
            lease_expires_at__gt=now,
            access_grant__revoked_at__isnull=True,
        )
        .first()
    )
    if delivery is None:
        return None
    if delivery.recipient_email != delivery.order.delivery_email:
        _cancel_obsolete_active_claim(claim=claim, now=now)
        return None
    if delivery.order.status != Order.Status.PAID:
        return None
    return delivery


def _cancel_obsolete_active_claim(*, claim: EmailDeliveryClaim, now: datetime) -> None:
    """Cancel only a still-current claim that has not crossed external I/O."""
    with transaction.atomic():
        delivery = (
            EmailDelivery.objects.select_for_update()
            .select_related("order")
            .filter(
                pk=claim.delivery_id,
                state=EmailDelivery.State.PROCESSING,
                lease_id=claim.lease_id,
                lease_expires_at__gt=now,
            )
            .first()
        )
        if delivery is None or delivery.recipient_email == delivery.order.delivery_email:
            return
        delivery.state = EmailDelivery.State.CANCELED
        delivery.lease_id = None
        delivery.lease_expires_at = None
        delivery.save(update_fields=["state", "lease_id", "lease_expires_at", "updated_at"])


def _customer_access_message(
    *,
    delivery: EmailDelivery,
    order_access_signing_secret: str | bytes,
    order_access_url_for_grant: Callable[[OrderAccessGrant, str], str],
    support_contact: str,
) -> EmailMessage:
    if not isinstance(support_contact, str) or not support_contact.strip():
        raise ValueError("Customer support contact is required.")
    signature = sign_order_access_grant(
        grant=delivery.access_grant,
        signing_secret=order_access_signing_secret,
    )
    access_url = order_access_url_for_grant(delivery.access_grant, signature)
    if not isinstance(access_url, str) or not access_url.startswith("https://"):
        raise ValueError("Order access URL must be an absolute HTTPS URL.")
    order = delivery.order
    return EmailMessage(
        recipient_email=delivery.recipient_email,
        subject=f"Ваши фотографии с мероприятия «{order.event.name}»",
        text_body="\n".join(
            (
                "Здравствуйте!",
                "",
                f"Заказ {order.public_number} от {order.created_at:%d.%m.%Y} оплачен.",
                f"Фотографий: {order.items.count()}",
                f"Итого: {format_rub(order.total_kopecks)}",
                "",
                "Открыть оригиналы:",
                access_url,
                "Не пересылайте эту секретную ссылку: по ней можно открыть оригиналы.",
                "",
                f"Поддержка: {support_contact.strip()}",
            )
        ),
    )


def _record_delivery_result(
    *,
    claim: EmailDeliveryClaim,
    result: EmailSendResult,
    now: datetime,
) -> EmailDelivery | None:
    with transaction.atomic():
        delivery = (
            EmailDelivery.objects.select_for_update()
            .select_related("order", "access_grant")
            .filter(
                pk=claim.delivery_id,
                state=EmailDelivery.State.PROCESSING,
                lease_id=claim.lease_id,
                lease_expires_at__gt=now,
            )
            .first()
        )
        if delivery is None:
            return None
        attempt_number = delivery.attempt_count + 1
        outcome = _attempt_outcome(result.outcome)
        EmailDeliveryAttempt.objects.create(
            delivery=delivery,
            attempt_number=attempt_number,
            recipient_email=delivery.recipient_email,
            outcome=outcome,
            safe_failure_category=result.safe_failure_category,
            attempted_at=now,
        )
        delivery.attempt_count = attempt_number
        delivery.lease_id = None
        delivery.lease_expires_at = None
        if result.outcome == EmailSendOutcome.SUCCEEDED:
            delivery.state = EmailDelivery.State.SUCCEEDED
            delivery.last_failure_category = ""
            delivery.save(
                update_fields=[
                    "attempt_count",
                    "state",
                    "lease_id",
                    "lease_expires_at",
                    "last_failure_category",
                    "updated_at",
                ]
            )
            resolve_open_attention_automatically(
                kind="email_exhausted",
                subject=f"email-delivery:{delivery.pk}",
                now=now,
            )
            if delivery.access_grant.source == OrderAccessGrant.Source.RESEND:
                _resolve_repaired_email_failure_attentions(order=delivery.order, now=now)
            return delivery

        delivery.last_failure_category = result.safe_failure_category
        if delivery.recipient_email != delivery.order.delivery_email:
            delivery.state = EmailDelivery.State.CANCELED
            delivery.save(
                update_fields=[
                    "attempt_count",
                    "state",
                    "lease_id",
                    "lease_expires_at",
                    "last_failure_category",
                    "updated_at",
                ]
            )
            return delivery
        if result.outcome == EmailSendOutcome.RETRYABLE_FAILURE and attempt_number <= len(
            _RETRY_DELAYS
        ):
            delivery.state = EmailDelivery.State.RETRY_WAIT
            delivery.next_attempt_at = now + _RETRY_DELAYS[attempt_number - 1]
            delivery.save(
                update_fields=[
                    "attempt_count",
                    "state",
                    "lease_id",
                    "lease_expires_at",
                    "next_attempt_at",
                    "last_failure_category",
                    "updated_at",
                ]
            )
            return delivery

        delivery.state = EmailDelivery.State.FAILED
        delivery.save(
            update_fields=[
                "attempt_count",
                "state",
                "lease_id",
                "lease_expires_at",
                "last_failure_category",
                "updated_at",
            ]
        )
        open_attention(
            kind="email_exhausted",
            subject=f"email-delivery:{delivery.pk}",
            order=delivery.order,
            now=now,
        )
        return delivery


def _resolve_repaired_email_failure_attentions(*, order: Order, now: datetime) -> None:
    """Close only exhausted predecessor deliveries that this successful resend repaired."""
    failed_delivery_ids = EmailDelivery.objects.filter(
        order=order,
        state__in=(EmailDelivery.State.FAILED, EmailDelivery.State.CANCELED),
    ).values_list("pk", flat=True)
    for failed_delivery_id in failed_delivery_ids:
        resolve_open_attention_automatically(
            kind="email_exhausted",
            subject=f"email-delivery:{failed_delivery_id}",
            now=now,
        )


def _attempt_outcome(outcome: EmailSendOutcome) -> str:
    return {
        EmailSendOutcome.SUCCEEDED: str(EmailDeliveryAttempt.Outcome.SUCCEEDED),
        EmailSendOutcome.RETRYABLE_FAILURE: str(EmailDeliveryAttempt.Outcome.RETRYABLE_FAILURE),
        EmailSendOutcome.TERMINAL_FAILURE: str(EmailDeliveryAttempt.Outcome.TERMINAL_FAILURE),
    }[outcome]


def _normalized_delivery_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Delivery email is required.")
    normalized = value.strip().casefold()
    try:
        validate_email(normalized)
    except ValidationError as error:
        raise ValueError("Delivery email is invalid.") from error
    return normalized


def _validate_claim_request(*, limit: int, lease_duration: timedelta) -> None:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= _MAX_DELIVERY_CLAIM_LIMIT
    ):
        raise ValueError("Delivery claim limit must be bounded.")
    if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
        raise ValueError("Delivery lease duration must be positive.")


def _current_time(now: datetime | None) -> datetime:
    return now or timezone.now()
