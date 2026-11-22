"""Observations of the durable reconciliation cycle."""

from datetime import datetime

from config.queue_timings import elapsed_ms

from commerce.models import PaymentAttempt


def work_state(attempt: PaymentAttempt) -> str | None:
    if (
        attempt.status != PaymentAttempt.Status.PENDING
        or attempt.reconciliation_next_attempt_at is None
    ):
        return None
    if attempt.reconciliation_state == PaymentAttempt.ReconciliationState.PROCESSING:
        return "processing"
    return "retry_wait" if attempt.queue_first_claimed_at is not None else "pending"


def segments(attempt: PaymentAttempt, finished: datetime) -> dict[str, int] | None:
    submitted, first = attempt.queue_enqueued_at, attempt.queue_first_claimed_at
    gateway, accept = attempt.queue_gateway_ms, attempt.queue_accept_ms
    if (
        submitted is None
        or first is None
        or gateway is None
        or accept is None
        or not submitted <= first <= finished
    ):
        return None
    wait = elapsed_ms(submitted, first)
    interval = elapsed_ms(submitted, finished) - wait - gateway - accept
    if interval < 0:
        return None
    return {"wait": wait, "gateway": gateway, "accept": accept, "interval": interval}


def record_phases(attempt: PaymentAttempt, *, gateway: int = 0, accept: int = 0) -> None:
    if attempt.queue_gateway_ms is None or attempt.queue_accept_ms is None:
        return
    attempt.queue_gateway_ms += gateway
    attempt.queue_accept_ms += accept
    attempt.save(update_fields=["queue_gateway_ms", "queue_accept_ms"])
