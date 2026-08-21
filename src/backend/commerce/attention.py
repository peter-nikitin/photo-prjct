import logging
import re
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.models import CommerceAttention, Order, PaymentAttempt

logger = logging.getLogger(__name__)

_SAFE_SUBJECT_RE = re.compile(r"^[a-z][a-z0-9_-]*:[a-z0-9_-]+$")


def _safe_subject(subject: str) -> str:
    if not isinstance(subject, str) or not _SAFE_SUBJECT_RE.fullmatch(subject):
        raise ValueError("Attention subject must be a safe reference.")
    return subject


def _current_time(now: datetime | None) -> datetime:
    return now or timezone.now()


def open_attention(
    *,
    kind: str,
    subject: str,
    order: Order | None = None,
    payment_attempt: PaymentAttempt | None = None,
    now: datetime | None = None,
) -> CommerceAttention:
    """Open one durable problem, or record another observation of the same open problem."""
    if kind not in CommerceAttention.Kind.values:
        raise ValueError("Attention kind must be an initial Commerce attention kind.")
    subject = _safe_subject(subject)
    observed_at = _current_time(now)

    with transaction.atomic():
        attention = (
            CommerceAttention.objects.select_for_update()
            .filter(kind=kind, subject=subject, resolved_at__isnull=True)
            .first()
        )
        opened = attention is None
        if attention is None:
            try:
                with transaction.atomic():
                    attention = CommerceAttention.objects.create(
                        kind=kind,
                        subject=subject,
                        order=order,
                        payment_attempt=payment_attempt,
                        first_observed_at=observed_at,
                        last_observed_at=observed_at,
                        next_reminder_at=observed_at,
                    )
            except IntegrityError:
                attention = CommerceAttention.objects.select_for_update().get(
                    kind=kind,
                    subject=subject,
                    resolved_at__isnull=True,
                )
                opened = False

        if not opened:
            attention.last_observed_at = observed_at
            attention.save(update_fields=["last_observed_at"])

        if opened:
            payload = {
                "kind": kind,
                "subject": subject,
                "order_public_number": order.public_number if order is not None else None,
                "payment_attempt_id": payment_attempt.pk if payment_attempt is not None else None,
            }
            transaction.on_commit(
                lambda: logger.info(
                    "commerce_attention_opened",
                    extra={"commerce_attention": payload},
                )
            )
    return attention


def resolve_attention_automatically(
    *,
    attention_id: int,
    now: datetime | None = None,
) -> CommerceAttention:
    """Close an open attention record only after a verified automatic repair."""
    return _resolve_attention(
        attention_id=attention_id,
        source="automatic",
        comment="",
        now=now,
    )


def resolve_attention_manually(
    *,
    attention_id: int,
    comment: str,
    now: datetime | None = None,
) -> CommerceAttention:
    """Preserve an operator's explicit reason when they close a remaining problem."""
    if not isinstance(comment, str) or not (comment := comment.strip()):
        raise ValueError("A manual attention resolution requires a comment.")
    return _resolve_attention(
        attention_id=attention_id,
        source="admin",
        comment=comment,
        now=now,
    )


def resolve_open_attention_automatically(
    *,
    kind: str,
    subject: str,
    now: datetime | None = None,
) -> CommerceAttention | None:
    """Resolve one matching open record, if a current repair makes that safe."""
    subject = _safe_subject(subject)
    with transaction.atomic():
        attention = (
            CommerceAttention.objects.select_for_update()
            .filter(kind=kind, subject=subject, resolved_at__isnull=True)
            .first()
        )
        if attention is None:
            return None
        attention.resolved_at = _current_time(now)
        attention.resolution_source = CommerceAttention.ResolutionSource.AUTOMATIC
        attention.resolution_comment = ""
        attention.save(update_fields=["resolved_at", "resolution_source", "resolution_comment"])
    return attention


def _resolve_attention(
    *,
    attention_id: int,
    source: str,
    comment: str,
    now: datetime | None,
) -> CommerceAttention:
    with transaction.atomic():
        attention = CommerceAttention.objects.select_for_update().get(pk=attention_id)
        if attention.resolved_at is not None:
            return attention
        attention.resolved_at = _current_time(now)
        attention.resolution_source = source
        attention.resolution_comment = comment
        attention.save(update_fields=["resolved_at", "resolution_source", "resolution_comment"])
    return attention
