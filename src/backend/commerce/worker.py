import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone

from commerce.attention import open_attention
from commerce.delivery import claim_due_email_deliveries, send_claimed_email_delivery
from commerce.email_sender import EmailMessage, EmailSender, EmailSendResult
from commerce.models import CommerceAttention, EmailDelivery, OrderAccessGrant, PaymentAttempt
from commerce.payment_gateway import PaymentGateway
from commerce.payments import (
    PaymentReconciliationUnavailable,
    PaymentTransitionRejected,
    reconcile_payment_attempt,
)

logger = logging.getLogger(__name__)

_MAX_CLAIM_LIMIT = 20
_ATTENTION_REMINDER_INTERVAL = timedelta(hours=24)
_PAYMENT_FALLBACK_EXPIRY = timedelta(hours=24)
_PAYMENT_RECONCILIATION_LEASE_DURATION = timedelta(minutes=5)
_PAYMENT_RECONCILIATION_RETRY_DELAY = timedelta(minutes=5)
_WORKER_ADVISORY_LOCK = 806_220_01


@dataclass(frozen=True)
class CommerceWorkerRun:
    email_deliveries: int
    payment_reconciliations: int
    attention_reminders: int


@dataclass(frozen=True)
class CommerceWorkerHealth:
    healthy: bool
    worker_alive: bool
    oldest_ready_work_type: str | None
    oldest_ready_age: timedelta | None


@dataclass(frozen=True)
class _AttentionReminderClaim:
    attention_id: int


@dataclass(frozen=True)
class PaymentReconciliationClaim:
    """The exact short-lived PostgreSQL claim for one provider status fetch."""

    attempt_id: int
    lease_id: UUID


class CommerceWorker:
    """One small poller with two explicit Commerce work types and safe operator reminders."""

    def __init__(
        self,
        *,
        email_sender: EmailSender,
        payment_gateway: PaymentGateway,
        order_access_signing_secret: str | bytes,
        order_access_url_for_grant: Callable[[OrderAccessGrant, str], str],
        support_contact: str,
        admin_url_for_attention: Callable[[CommerceAttention], str],
        claim_limit: int = _MAX_CLAIM_LIMIT,
        email_timeout_seconds: int = 20,
    ) -> None:
        if not 1 <= claim_limit <= _MAX_CLAIM_LIMIT:
            raise ValueError("Commerce worker claim limit must be bounded.")
        if not isinstance(email_timeout_seconds, int) or email_timeout_seconds <= 0:
            raise ValueError("Commerce email timeout must be positive.")
        self._email_sender = email_sender
        self._payment_gateway = payment_gateway
        self._order_access_signing_secret = order_access_signing_secret
        self._order_access_url_for_grant = order_access_url_for_grant
        self._support_contact = support_contact
        self._admin_url_for_attention = admin_url_for_attention
        self._claim_limit = claim_limit
        self._email_timeout_seconds = email_timeout_seconds

    def run_once(self, *, now: datetime | None = None) -> CommerceWorkerRun:
        delivered = self._process_email_deliveries(now=now)
        reconciled = self._process_payment_reconciliations(now=now)
        reminded = self._process_attention_reminders(now=now)
        return CommerceWorkerRun(
            email_deliveries=delivered,
            payment_reconciliations=reconciled,
            attention_reminders=reminded,
        )

    def _process_email_deliveries(self, *, now: datetime | None) -> int:
        processed = 0
        claim_time = now or timezone.now()
        for claim in claim_due_email_deliveries(
            now=claim_time,
            limit=self._claim_limit,
            lease_duration=self._email_delivery_lease_duration(),
        ):
            delivery = send_claimed_email_delivery(
                claim=claim,
                email_sender=self._email_sender,
                order_access_signing_secret=self._order_access_signing_secret,
                order_access_url_for_grant=self._order_access_url_for_grant,
                support_contact=self._support_contact,
                timeout_seconds=self._email_timeout_seconds,
                now=now,
            )
            if delivery is None:
                continue
            processed += 1
            logger.info(
                "commerce_worker_processed",
                extra={
                    "commerce_worker": {
                        "work_type": "email_delivery",
                        "delivery_id": delivery.pk,
                        "state": delivery.state,
                    }
                },
            )
        return processed

    def _email_delivery_lease_duration(self) -> timedelta:
        return max(
            timedelta(minutes=5),
            timedelta(seconds=self._claim_limit * self._email_timeout_seconds + 60),
        )

    def _process_payment_reconciliations(self, *, now: datetime | None) -> int:
        processed = 0
        claim_time = now or timezone.now()
        for claim in claim_due_payment_reconciliations(
            now=claim_time,
            limit=self._claim_limit,
            adapter_key=self._payment_gateway.adapter_key,
        ):
            try:
                reconcile_payment_attempt(
                    attempt_id=claim.attempt_id,
                    gateway=self._payment_gateway,
                    now=now,
                    expected_reconciliation_lease_id=claim.lease_id,
                )
            except PaymentReconciliationUnavailable as error:
                _release_payment_reconciliation_claim(
                    claim=claim,
                    now=now or timezone.now(),
                )
                logger.warning(
                    "commerce_worker_reconciliation_unavailable",
                    extra={
                        "commerce_worker": {
                            "work_type": "payment_reconciliation",
                            "payment_attempt_id": claim.attempt_id,
                            "failure_category": str(error),
                        }
                    },
                )
                continue
            except PaymentTransitionRejected:
                released_at = now or timezone.now()
                if _release_payment_reconciliation_claim(claim=claim, now=released_at):
                    _open_reconciliation_attention_if_still_pending(
                        attempt_id=claim.attempt_id,
                        now=released_at,
                    )
                logger.warning(
                    "commerce_worker_reconciliation_rejected",
                    extra={
                        "commerce_worker": {
                            "work_type": "payment_reconciliation",
                            "payment_attempt_id": claim.attempt_id,
                        }
                    },
                )
                continue
            processed += 1
            logger.info(
                "commerce_worker_processed",
                extra={
                    "commerce_worker": {
                        "work_type": "payment_reconciliation",
                        "payment_attempt_id": claim.attempt_id,
                    }
                },
            )
        return processed

    def _process_attention_reminders(self, *, now: datetime | None) -> int:
        processed = 0
        claim_time = now or timezone.now()
        for claim in _claim_due_attention_reminders(now=claim_time, limit=self._claim_limit):
            attention = CommerceAttention.objects.select_related("order").get(pk=claim.attention_id)
            message = _attention_message(
                attention=attention,
                admin_url_for_attention=self._admin_url_for_attention,
            )
            for recipient in _attention_recipients():
                try:
                    result = self._email_sender.send(
                        EmailMessage(
                            recipient_email=recipient,
                            subject=message.subject,
                            text_body=message.text_body,
                        ),
                        timeout_seconds=self._email_timeout_seconds,
                    )
                    if not isinstance(result, EmailSendResult):
                        raise TypeError("Email sender result is not normalized.")
                except Exception:
                    logger.warning(
                        "commerce_worker_attention_notification_failed",
                        extra={
                            "commerce_worker": {
                                "work_type": "attention_reminder",
                                "attention_id": attention.pk,
                            }
                        },
                    )
            processed += 1
            logger.info(
                "commerce_worker_processed",
                extra={
                    "commerce_worker": {
                        "work_type": "attention_reminder",
                        "attention_id": attention.pk,
                    }
                },
            )
        return processed


def commerce_worker_health(
    *,
    now: datetime | None = None,
    max_ready_age: timedelta,
    worker_is_alive: Callable[[], bool],
) -> CommerceWorkerHealth:
    """Read only queue age and liveness for an independent deployed monitor."""
    if not isinstance(max_ready_age, timedelta) or max_ready_age < timedelta(0):
        raise ValueError("Commerce ready-work threshold must be non-negative.")
    current_time = now or timezone.now()
    oldest_type, oldest_due_at = _oldest_ready_work(now=current_time)
    oldest_age = current_time - oldest_due_at if oldest_due_at is not None else None
    worker_alive = worker_is_alive()
    return CommerceWorkerHealth(
        healthy=worker_alive and (oldest_age is None or oldest_age <= max_ready_age),
        worker_alive=worker_alive,
        oldest_ready_work_type=oldest_type,
        oldest_ready_age=oldest_age,
    )


def commerce_worker_is_alive() -> bool:
    """Check a dedicated PostgreSQL advisory lock without writing any durable state."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_WORKER_ADVISORY_LOCK])
        acquired = bool(cursor.fetchone()[0])
        if acquired:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [_WORKER_ADVISORY_LOCK])
    return not acquired


def acquire_commerce_worker_lock() -> bool:
    """Acquire the single process liveness lock for a long-running Commerce worker."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_WORKER_ADVISORY_LOCK])
        return bool(cursor.fetchone()[0])


def release_commerce_worker_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [_WORKER_ADVISORY_LOCK])


def claim_due_payment_reconciliations(
    *,
    now: datetime,
    limit: int,
    adapter_key: str,
) -> tuple[PaymentReconciliationClaim, ...]:
    """Recover and lease a bounded batch of due payment status reconciliations."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_CLAIM_LIMIT:
        raise ValueError("Payment reconciliation claim limit must be bounded.")
    if not isinstance(adapter_key, str) or not adapter_key:
        raise ValueError("Payment reconciliation adapter key is required.")
    with transaction.atomic():
        _recover_expired_payment_reconciliation_leases(now=now, limit=limit)
        attempts = list(
            PaymentAttempt.objects.select_for_update(skip_locked=True)
            .filter(
                status=PaymentAttempt.Status.PENDING,
                reconciliation_state=PaymentAttempt.ReconciliationState.PENDING,
                adapter_key=adapter_key,
            )
            .filter(reconciliation_next_attempt_at__lte=now)
            .order_by("reconciliation_next_attempt_at", "pk")[:limit]
        )
        claims: list[PaymentReconciliationClaim] = []
        for attempt in attempts:
            lease_id = uuid4()
            attempt.reconciliation_state = PaymentAttempt.ReconciliationState.PROCESSING
            attempt.reconciliation_lease_id = lease_id
            attempt.reconciliation_lease_expires_at = now + _PAYMENT_RECONCILIATION_LEASE_DURATION
            attempt.save(
                update_fields=[
                    "reconciliation_state",
                    "reconciliation_lease_id",
                    "reconciliation_lease_expires_at",
                    "updated_at",
                ]
            )
            claims.append(PaymentReconciliationClaim(attempt_id=attempt.pk, lease_id=lease_id))
    return tuple(claims)


def _recover_expired_payment_reconciliation_leases(*, now: datetime, limit: int) -> None:
    expired_attempts = list(
        PaymentAttempt.objects.select_for_update(skip_locked=True)
        .filter(
            status=PaymentAttempt.Status.PENDING,
            reconciliation_state=PaymentAttempt.ReconciliationState.PROCESSING,
            reconciliation_lease_expires_at__lte=now,
        )
        .order_by("reconciliation_lease_expires_at", "pk")[:limit]
    )
    for attempt in expired_attempts:
        attempt.reconciliation_state = PaymentAttempt.ReconciliationState.PENDING
        attempt.reconciliation_lease_id = None
        attempt.reconciliation_lease_expires_at = None
        attempt.reconciliation_next_attempt_at = now
        attempt.save(
            update_fields=[
                "reconciliation_state",
                "reconciliation_lease_id",
                "reconciliation_lease_expires_at",
                "reconciliation_next_attempt_at",
                "updated_at",
            ]
        )


def _release_payment_reconciliation_claim(
    *,
    claim: PaymentReconciliationClaim,
    now: datetime,
) -> bool:
    """Fence a failed fetch and schedule its one bounded automatic retry."""
    with transaction.atomic():
        attempt = (
            PaymentAttempt.objects.select_for_update()
            .filter(
                pk=claim.attempt_id,
                status=PaymentAttempt.Status.PENDING,
                reconciliation_state=PaymentAttempt.ReconciliationState.PROCESSING,
                reconciliation_lease_id=claim.lease_id,
                reconciliation_lease_expires_at__gt=now,
            )
            .first()
        )
        if attempt is None:
            return False
        original_due_at = attempt.expires_at or attempt.created_at + _PAYMENT_FALLBACK_EXPIRY
        already_retried = (
            attempt.reconciliation_next_attempt_at is not None
            and attempt.reconciliation_next_attempt_at > original_due_at
        )
        attempt.reconciliation_state = PaymentAttempt.ReconciliationState.PENDING
        attempt.reconciliation_lease_id = None
        attempt.reconciliation_lease_expires_at = None
        attempt.reconciliation_next_attempt_at = (
            None if already_retried else now + _PAYMENT_RECONCILIATION_RETRY_DELAY
        )
        attempt.save(
            update_fields=[
                "reconciliation_state",
                "reconciliation_lease_id",
                "reconciliation_lease_expires_at",
                "reconciliation_next_attempt_at",
                "updated_at",
            ]
        )
    return True


def _open_reconciliation_attention_if_still_pending(*, attempt_id: int, now: datetime) -> None:
    attempt = PaymentAttempt.objects.select_related("order").filter(pk=attempt_id).first()
    if attempt is None or attempt.status != PaymentAttempt.Status.PENDING:
        return
    open_attention(
        kind=str(CommerceAttention.Kind.PAYMENT_RECONCILIATION_OVERDUE),
        subject=f"payment-attempt:{attempt.pk}",
        order=attempt.order,
        payment_attempt=attempt,
        now=now,
    )


def _claim_due_attention_reminders(
    *,
    now: datetime,
    limit: int,
) -> tuple[_AttentionReminderClaim, ...]:
    with transaction.atomic():
        attentions = list(
            CommerceAttention.objects.select_for_update(skip_locked=True)
            .filter(resolved_at__isnull=True, next_reminder_at__lte=now)
            .order_by("next_reminder_at", "pk")[:limit]
        )
        for attention in attentions:
            attention.next_reminder_at = now + _ATTENTION_REMINDER_INTERVAL
            attention.save(update_fields=["next_reminder_at"])
    return tuple(_AttentionReminderClaim(attention_id=attention.pk) for attention in attentions)


def _attention_recipients() -> tuple[str, ...]:
    recipients: list[str] = []
    users = (
        get_user_model()._default_manager.filter(is_active=True, is_staff=True).exclude(email="")
    )
    for user in users:
        if user.has_perm("commerce.handle_attention"):
            recipients.append(user.email)
    return tuple(recipients)


def _attention_message(
    *,
    attention: CommerceAttention,
    admin_url_for_attention: Callable[[CommerceAttention], str],
) -> EmailMessage:
    admin_url = admin_url_for_attention(attention)
    if not isinstance(admin_url, str) or not admin_url.startswith("https://"):
        raise ValueError("Commerce attention URL must be an absolute HTTPS URL.")
    order_number = attention.order.public_number if attention.order_id is not None else "нет"
    return EmailMessage(
        recipient_email="operator@example.invalid",
        subject=f"Требуется внимание Commerce: {attention.kind}",
        text_body="\n".join(
            (
                "Требуется внимание Commerce.",
                f"Проблема: {attention.kind}",
                f"Заказ: {order_number}",
                "Открыть в Admin:",
                admin_url,
            )
        ),
    )


def _oldest_ready_work(*, now: datetime) -> tuple[str | None, datetime | None]:
    candidates: list[tuple[str, datetime]] = []
    email_due_at = (
        EmailDelivery.objects.filter(
            state__in=(EmailDelivery.State.PENDING, EmailDelivery.State.RETRY_WAIT),
            next_attempt_at__lte=now,
        )
        .order_by("next_attempt_at")
        .values_list("next_attempt_at", flat=True)
        .first()
    )
    if email_due_at is not None:
        candidates.append(("email_delivery", email_due_at))
    expired_email_lease_at = (
        EmailDelivery.objects.filter(
            state=EmailDelivery.State.PROCESSING,
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at")
        .values_list("lease_expires_at", flat=True)
        .first()
    )
    if expired_email_lease_at is not None:
        candidates.append(("email_delivery", expired_email_lease_at))
    payment_due_at = (
        PaymentAttempt.objects.filter(
            status=PaymentAttempt.Status.PENDING,
            reconciliation_state=PaymentAttempt.ReconciliationState.PENDING,
            reconciliation_next_attempt_at__lte=now,
        )
        .order_by("reconciliation_next_attempt_at", "pk")
        .values_list("reconciliation_next_attempt_at", flat=True)
        .first()
    )
    if payment_due_at is not None:
        candidates.append(("payment_reconciliation", payment_due_at))
    expired_payment_lease_at = (
        PaymentAttempt.objects.filter(
            status=PaymentAttempt.Status.PENDING,
            reconciliation_state=PaymentAttempt.ReconciliationState.PROCESSING,
            reconciliation_lease_expires_at__lte=now,
        )
        .order_by("reconciliation_lease_expires_at")
        .values_list("reconciliation_lease_expires_at", flat=True)
        .first()
    )
    if expired_payment_lease_at is not None:
        candidates.append(("payment_reconciliation", expired_payment_lease_at))
    if not candidates:
        return None, None
    return min(candidates, key=lambda candidate: candidate[1])
