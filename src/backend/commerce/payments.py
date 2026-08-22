from datetime import datetime, timedelta
from uuid import UUID

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Model
from django.utils import timezone
from picflow.models import Event

from commerce.attention import open_attention, resolve_open_attention_automatically
from commerce.models import (
    Cart,
    CartItem,
    EmailDelivery,
    Order,
    OrderAccessGrant,
    PaymentAttempt,
    PaymentEvidence,
)
from commerce.payment_gateway import (
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentGateway,
    PaymentGatewayError,
    PaymentObservation,
)


class PaymentTransitionRejected(Exception):
    """A safe rejection of evidence that cannot change an Order."""


class PaymentReconciliationUnavailable(PaymentTransitionRejected):
    """A due provider payment could not be safely reconciled."""


def apply_authenticated_notification(
    *,
    gateway: PaymentGateway,
    notification: IncomingPaymentNotification,
    now: datetime | None = None,
) -> Order:
    """Authenticate provider input outside the transaction, then apply its normalized evidence."""
    observation = gateway.authenticate_notification(notification)
    attempt = _matching_attempt_for_gateway(gateway=gateway, observation=observation)
    return apply_payment_observation(
        attempt_id=attempt.pk,
        adapter_key=gateway.adapter_key,
        source="notification",
        observation=observation,
        now=now,
    )


def reconcile_payment_attempt(
    *,
    attempt_id: int,
    gateway: PaymentGateway,
    now: datetime | None = None,
    expected_reconciliation_lease_id: UUID | None = None,
) -> Order:
    """Fetch one due payment outside the transaction before serializing its state transition."""
    started_at = _current_time(now)
    attempt = PaymentAttempt.objects.get(pk=attempt_id)
    if not attempt.provider_payment_id or attempt.adapter_key != _gateway_adapter_key(gateway):
        raise PaymentTransitionRejected("Payment attempt cannot be reconciled by this gateway.")
    if expected_reconciliation_lease_id is not None and not _reconciliation_lease_is_current(
        attempt=attempt,
        lease_id=expected_reconciliation_lease_id,
        now=started_at,
    ):
        raise PaymentTransitionRejected("Payment reconciliation lease is no longer current.")
    try:
        observation = gateway.fetch_payment(attempt.provider_payment_id)
    except PaymentGatewayError as error:
        unavailable_at = _current_time(now)
        open_attention(
            kind="payment_reconciliation_overdue",
            subject=f"payment-attempt:{attempt.pk}",
            order=attempt.order,
            payment_attempt=attempt,
            now=unavailable_at,
        )
        raise PaymentReconciliationUnavailable(error.category.value) from None

    completed_at = _current_time(now)
    order = apply_payment_observation(
        attempt_id=attempt.pk,
        adapter_key=gateway.adapter_key,
        source="status_fetch",
        observation=observation,
        now=completed_at,
        expected_reconciliation_lease_id=expected_reconciliation_lease_id,
    )
    resolve_open_attention_automatically(
        kind="payment_reconciliation_overdue",
        subject=f"payment-attempt:{attempt.pk}",
        now=completed_at,
    )
    _expire_after_current_fetch(
        attempt_id=attempt.pk,
        now=completed_at,
        expected_reconciliation_lease_id=expected_reconciliation_lease_id,
    )
    return order


def apply_payment_observation(
    *,
    attempt_id: int,
    adapter_key: str,
    source: str,
    observation: PaymentObservation,
    now: datetime | None = None,
    expected_reconciliation_lease_id: UUID | None = None,
) -> Order:
    """Serialize every normalized callback and fetch through one authoritative transition."""
    if source not in PaymentEvidence.Source.values:
        raise PaymentTransitionRejected("Payment evidence source is not supported.")
    if not isinstance(observation, PaymentObservation):
        raise PaymentTransitionRejected("Payment evidence must be normalized.")
    current_time = _current_time(now)

    event_id, cart_digest, order_id = _payment_identity_for_attempt(attempt_id=attempt_id)
    with transaction.atomic():
        cart, order, attempt = _lock_payment_transition(
            event_id=event_id,
            cart_digest=cart_digest,
            order_id=order_id,
            attempt_id=attempt_id,
        )
        if attempt is None:
            raise PaymentTransitionRejected("Payment attempt lock is unavailable.")
        if expected_reconciliation_lease_id is not None and not _reconciliation_lease_is_current(
            attempt=attempt,
            lease_id=expected_reconciliation_lease_id,
            now=current_time,
        ):
            raise PaymentTransitionRejected("Payment reconciliation lease is no longer current.")
        _require_matching_provider_evidence(
            attempt=attempt,
            adapter_key=adapter_key,
            observation=observation,
        )

        if _payment_facts_mismatch(attempt=attempt, order=order, observation=observation):
            _record_observation_if_persistable(
                attempt=attempt,
                source=source,
                observation=observation,
                observed_at=current_time,
            )
            _set_attempt_terminal(
                attempt=attempt,
                status="conflict",
                terminal_at=current_time,
            )
            attention_kind: str = (
                "manual_payment_conflict"
                if order.status == Order.Status.PAID
                else "payment_mismatch"
            )
            open_attention(
                kind=attention_kind,
                subject=f"payment-attempt:{attempt.pk}",
                order=order,
                payment_attempt=attempt,
                now=current_time,
            )
            return order

        _record_observation_if_persistable(
            attempt=attempt,
            source=source,
            observation=observation,
            observed_at=current_time,
        )
        if observation.status == NormalizedPaymentStatus.SUCCEEDED:
            if order.status == Order.Status.CANCELED:
                _set_attempt_terminal(
                    attempt=attempt,
                    status="conflict",
                    terminal_at=current_time,
                )
                open_attention(
                    kind="payment_mismatch",
                    subject=f"payment-attempt:{attempt.pk}",
                    order=order,
                    payment_attempt=attempt,
                    now=current_time,
                )
                return order

            _set_attempt_terminal(
                attempt=attempt,
                status="succeeded",
                terminal_at=current_time,
            )
            _fulfill_paid_order(order=order, cart=cart, paid_at=current_time)
            resolve_open_attention_automatically(
                kind="payment_mismatch",
                subject=f"payment-attempt:{attempt.pk}",
                now=current_time,
            )
            resolve_open_attention_automatically(
                kind="manual_payment_conflict",
                subject=f"payment-attempt:{attempt.pk}",
                now=current_time,
            )
            return order

        if observation.status == NormalizedPaymentStatus.PENDING:
            return order

        terminal_status = {
            NormalizedPaymentStatus.CANCELED: "canceled",
            NormalizedPaymentStatus.EXPIRED: "expired",
            NormalizedPaymentStatus.FAILED: "failed",
        }[observation.status]
        _set_attempt_terminal(
            attempt=attempt,
            status=terminal_status,
            terminal_at=current_time,
        )
        if order.status == Order.Status.PAID:
            open_attention(
                kind="manual_payment_conflict",
                subject=f"payment-attempt:{attempt.pk}",
                order=order,
                payment_attempt=attempt,
                now=current_time,
            )
        return order


def mark_order_paid_manually(
    *,
    order_id: int,
    actor: object,
    now: datetime | None = None,
) -> Order:
    """Use the trusted-admin path, while sharing the same atomic fulfillment effects."""
    actor_id = _manual_actor_id(actor)
    event_id, cart_digest, immutable_order_id = _payment_identity_for_order(order_id=order_id)
    current_time = _current_time(now)
    with transaction.atomic():
        cart, order, _attempt = _lock_payment_transition(
            event_id=event_id,
            cart_digest=cart_digest,
            order_id=immutable_order_id,
            lock_all_attempts=True,
        )
        locked_actor = _lock_active_staff_actor(actor_id=actor_id)
        if order.status not in (Order.Status.PENDING, Order.Status.SUPERSEDED):
            raise PaymentTransitionRejected(
                "Only pending or superseded Orders may be paid manually."
            )
        _fulfill_paid_order(order=order, cart=cart, paid_at=current_time)
        _write_manual_order_audit(
            actor=locked_actor,
            order=order,
            change_message="Оплата подтверждена вручную.",
        )
    return order


def cancel_order(*, order_id: int, actor: object, now: datetime | None = None) -> Order:
    """Close only a pending Order; a paid or canceled obligation never reverses."""
    del now  # LogEntry supplies the atomic operator timestamp for this manual transition.
    actor_id = _manual_actor_id(actor)
    event_id, cart_digest, immutable_order_id = _payment_identity_for_order(order_id=order_id)
    with transaction.atomic():
        _cart, order, _attempt = _lock_payment_transition(
            event_id=event_id,
            cart_digest=cart_digest,
            order_id=immutable_order_id,
            lock_all_attempts=True,
        )
        locked_actor = _lock_active_staff_actor(actor_id=actor_id)
        if order.status != Order.Status.PENDING:
            raise PaymentTransitionRejected("Only pending Orders may be canceled.")
        order.status = Order.Status.CANCELED
        order.save(update_fields=["status"])
        _write_manual_order_audit(
            actor=locked_actor,
            order=order,
            change_message="Заказ отменен вручную.",
        )
    return order


def _matching_attempt_for_gateway(
    *,
    gateway: PaymentGateway,
    observation: PaymentObservation,
) -> PaymentAttempt:
    if not isinstance(observation, PaymentObservation):
        raise PaymentTransitionRejected("Payment evidence must be normalized.")
    attempt = (
        PaymentAttempt.objects.filter(
            adapter_key=_gateway_adapter_key(gateway),
            provider_payment_id=observation.provider_payment_id,
            idempotency_key=observation.idempotency_key,
        )
        .order_by("pk")
        .first()
    )
    if attempt is None:
        raise PaymentTransitionRejected("Payment evidence does not identify a current attempt.")
    return attempt


def _gateway_adapter_key(gateway: PaymentGateway) -> str:
    adapter_key = getattr(gateway, "adapter_key", None)
    if not isinstance(adapter_key, str) or not adapter_key:
        raise PaymentTransitionRejected("Payment gateway identity is unavailable.")
    return adapter_key


def _payment_identity_for_attempt(*, attempt_id: int) -> tuple[int, str, int]:
    """Resolve immutable relation IDs before acquiring the shared payment/cart lock order."""
    order_id, event_id, cart_digest = PaymentAttempt.objects.values_list(
        "order_id",
        "order__event_id",
        "order__originating_cart_token_sha256",
    ).get(pk=attempt_id)
    return event_id, cart_digest, order_id


def _payment_identity_for_order(*, order_id: int) -> tuple[int, str, int]:
    """Resolve immutable order identity before acquiring Event, Cart, Order, Attempt locks."""
    event_id, cart_digest = Order.objects.values_list(
        "event_id",
        "originating_cart_token_sha256",
    ).get(pk=order_id)
    return event_id, cart_digest, order_id


def _lock_payment_transition(
    *,
    event_id: int,
    cart_digest: str,
    order_id: int,
    attempt_id: int | None = None,
    lock_all_attempts: bool = False,
) -> tuple[Cart | None, Order, PaymentAttempt | None]:
    """Acquire the cart-command-compatible Event -> Cart -> Order -> Attempt locks."""
    if attempt_id is not None and lock_all_attempts:
        raise ValueError("Lock either one payment attempt or all order attempts.")
    Event.objects.select_for_update().get(pk=event_id)
    cart = (
        Cart.objects.select_for_update()
        .filter(event_id=event_id, browser_token_sha256=cart_digest)
        .first()
    )
    order = Order.objects.select_for_update().get(pk=order_id)
    if lock_all_attempts:
        list(PaymentAttempt.objects.select_for_update().filter(order=order).order_by("pk"))
        return cart, order, None
    if attempt_id is None:
        return cart, order, None
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.order_id != order.pk:
        raise PaymentTransitionRejected("Payment attempt does not belong to its locked Order.")
    return cart, order, attempt


def _manual_actor_id(actor: object) -> int:
    """Reject lookalikes before touching commercial state; re-check the DB in-transaction."""
    user_model = get_user_model()
    actor_id = getattr(actor, "pk", None)
    if (
        not isinstance(actor, user_model)
        or not isinstance(actor_id, int)
        or not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or not getattr(actor, "is_staff", False)
    ):
        raise PaymentTransitionRejected(
            "Manual payment confirmation requires an authenticated user."
        )
    return actor_id


def _lock_active_staff_actor(*, actor_id: int) -> Model:
    actor = (
        get_user_model()
        ._default_manager.select_for_update()
        .filter(pk=actor_id, is_active=True, is_staff=True)
        .first()
    )
    if actor is None:
        raise PaymentTransitionRejected("Manual payment confirmation requires active staff.")
    return actor


def _write_manual_order_audit(*, actor: Model, order: Order, change_message: str) -> None:
    LogEntry.objects.log_actions(
        user_id=actor.pk,
        queryset=[order],
        action_flag=CHANGE,
        change_message=change_message,
        single_object=True,
    )


def _require_matching_provider_evidence(
    *,
    attempt: PaymentAttempt,
    adapter_key: str,
    observation: PaymentObservation,
) -> None:
    if (
        attempt.adapter_key != adapter_key
        or attempt.provider_payment_id != observation.provider_payment_id
        or attempt.idempotency_key != observation.idempotency_key
    ):
        raise PaymentTransitionRejected("Payment evidence does not match this attempt.")


def _payment_facts_mismatch(
    *,
    attempt: PaymentAttempt,
    order: Order,
    observation: PaymentObservation,
) -> bool:
    return (
        order.currency != "RUB"
        or attempt.currency != "RUB"
        or observation.currency != "RUB"
        or observation.amount_kopecks != attempt.amount_kopecks
        or observation.amount_kopecks != order.total_kopecks
    )


def _record_observation_if_persistable(
    *,
    attempt: PaymentAttempt,
    source: str,
    observation: PaymentObservation,
    observed_at: datetime,
) -> None:
    if (
        not isinstance(observation.currency, str)
        or len(observation.currency) != 3
        or not observation.currency.isascii()
        or not observation.currency.isalpha()
        or observation.currency != observation.currency.upper()
        or not isinstance(observation.amount_kopecks, int)
        or isinstance(observation.amount_kopecks, bool)
        or observation.amount_kopecks <= 0
    ):
        return
    PaymentEvidence.objects.create(
        payment_attempt=attempt,
        source=source,
        provider_event_id=observation.provider_event_id,
        normalized_status=observation.status.value,
        amount_kopecks=observation.amount_kopecks,
        currency=observation.currency,
        observed_at=observed_at,
    )


def _set_attempt_terminal(
    *,
    attempt: PaymentAttempt,
    status: str,
    terminal_at: datetime,
) -> None:
    if attempt.status == status:
        return
    attempt.status = status
    attempt.terminal_at = terminal_at
    attempt.reconciliation_state = PaymentAttempt.ReconciliationState.PENDING
    attempt.reconciliation_lease_id = None
    attempt.reconciliation_lease_expires_at = None
    attempt.reconciliation_next_attempt_at = None
    attempt.save(
        update_fields=[
            "status",
            "terminal_at",
            "reconciliation_state",
            "reconciliation_lease_id",
            "reconciliation_lease_expires_at",
            "reconciliation_next_attempt_at",
            "updated_at",
        ]
    )


def _fulfill_paid_order(*, order: Order, cart: Cart | None, paid_at: datetime) -> None:
    if order.status == Order.Status.PAID:
        return
    order.status = Order.Status.PAID
    order.paid_at = paid_at
    order.save(update_fields=["status", "paid_at"])
    _create_initial_delivery(order=order, now=paid_at)
    _remove_only_originating_purchased_positions(order=order, cart=cart)


def _create_initial_delivery(*, order: Order, now: datetime) -> None:
    if EmailDelivery.objects.filter(
        order=order,
        message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
        access_grant__source=OrderAccessGrant.Source.CHECKOUT,
    ).exists():
        return
    grant = (
        OrderAccessGrant.objects.filter(order=order, source=OrderAccessGrant.Source.CHECKOUT)
        .order_by("created_at", "pk")
        .first()
    )
    if grant is None:
        raise PaymentTransitionRejected("Paid Order is missing its initial access grant.")
    EmailDelivery.objects.create(
        order=order,
        message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
        recipient_email=order.delivery_email,
        access_grant=grant,
        next_attempt_at=now,
    )


def _remove_only_originating_purchased_positions(*, order: Order, cart: Cart | None) -> None:
    if cart is None:
        return
    CartItem.objects.filter(
        cart=cart,
        photo_id__in=order.items.values_list("photo_id", flat=True),
    ).delete()
    if not CartItem.objects.filter(cart=cart).exists():
        cart.delete()


def _expire_after_current_fetch(
    *,
    attempt_id: int,
    now: datetime,
    expected_reconciliation_lease_id: UUID | None,
) -> None:
    event_id, cart_digest, order_id = _payment_identity_for_attempt(attempt_id=attempt_id)
    with transaction.atomic():
        _cart, order, attempt = _lock_payment_transition(
            event_id=event_id,
            cart_digest=cart_digest,
            order_id=order_id,
            attempt_id=attempt_id,
        )
        if attempt is None:
            raise PaymentTransitionRejected("Payment attempt lock is unavailable.")
        if attempt.status != PaymentAttempt.Status.PENDING:
            return
        if expected_reconciliation_lease_id is not None and not _reconciliation_lease_is_current(
            attempt=attempt,
            lease_id=expected_reconciliation_lease_id,
            now=now,
        ):
            raise PaymentTransitionRejected("Payment reconciliation lease is no longer current.")
        due_at = attempt.expires_at or attempt.created_at + timedelta(hours=24)
        if now < due_at:
            return
        _set_attempt_terminal(
            attempt=attempt,
            status="expired",
            terminal_at=now,
        )
        if order.status == Order.Status.PAID:
            open_attention(
                kind="manual_payment_conflict",
                subject=f"payment-attempt:{attempt.pk}",
                order=order,
                payment_attempt=attempt,
                now=now,
            )


def _reconciliation_lease_is_current(
    *,
    attempt: PaymentAttempt,
    lease_id: UUID,
    now: datetime,
) -> bool:
    return (
        attempt.status == PaymentAttempt.Status.PENDING
        and attempt.reconciliation_state == PaymentAttempt.ReconciliationState.PROCESSING
        and attempt.reconciliation_lease_id == lease_id
        and attempt.reconciliation_lease_expires_at is not None
        and attempt.reconciliation_lease_expires_at > now
    )


def _current_time(now: datetime | None) -> datetime:
    return now or timezone.now()
