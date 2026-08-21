import logging
from datetime import UTC, datetime, timedelta

from django.db import transaction
from django.test import TransactionTestCase
from picflow.models import Event, Photo

from commerce.attention import (
    open_attention,
    resolve_attention_automatically,
    resolve_attention_manually,
)
from commerce.models import CommerceAttention, Order, OrderItem, PaymentAttempt


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class CommerceAttentionServiceTests(TransactionTestCase):
    """The breaks caught here would hide an operator problem or expose customer data."""

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        self.event = Event.objects.create(
            name="Attention event",
            slug="attention-event",
            start_date=self.now.date(),
            end_date=self.now.date(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="attention-photo",
            event=self.event,
            src="photos/attention.jpg",
        )
        with transaction.atomic():
            self.order = Order.objects.create(
                public_number="FM-ATTENTN2",
                event=self.event,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
                purchase_browser_token_sha256="a" * 64,
            )
            self.item = OrderItem.objects.create(
                order=self.order,
                photo=self.photo,
                photo_public_id=self.photo.pk,
                unit_price_kopecks=30000,
                line_total_kopecks=30000,
            )
        self.attempt = PaymentAttempt.objects.create(
            order=self.order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="deterministic-test",
            idempotency_key="attention-attempt-1",
            provider_payment_id="provider-secret-not-for-logs",
        )

    def test_opens_each_initial_kind_with_only_safe_references(self) -> None:
        """A missing kind would leave one current operator failure without durable ownership."""
        subjects = {
            "payment_mismatch": f"payment-attempt:{self.attempt.pk}",
            "manual_payment_conflict": f"payment-attempt:{self.attempt.pk}",
            "original_missing": f"order-item:{self.item.pk}",
            "email_exhausted": "email-delivery:42",
            "payment_reconciliation_overdue": (f"payment-attempt:{self.attempt.pk}"),
            "commerce_work_stale": "commerce-work:email-delivery",
        }

        for kind, subject in subjects.items():
            with self.subTest(kind=kind):
                attention = open_attention(
                    kind=kind,
                    subject=subject,
                    order=self.order,
                    payment_attempt=self.attempt,
                    now=self.now,
                )
                self.assertEqual(attention.kind, kind)
                self.assertEqual(attention.subject, subject)
                self.assertEqual(attention.order_id, self.order.pk)
                self.assertEqual(attention.payment_attempt_id, self.attempt.pk)

    def test_open_is_transactional_and_duplicate_observations_update_one_open_record(self) -> None:
        """Opening outside the triggering transaction or duplicating rows would spam operators."""
        kind = "payment_mismatch"
        subject = f"payment-attempt:{self.attempt.pk}"
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                open_attention(
                    kind=kind,
                    subject=subject,
                    order=self.order,
                    payment_attempt=self.attempt,
                    now=self.now,
                )
                raise RuntimeError("rollback")
        self.assertFalse(CommerceAttention.objects.exists())

        first = open_attention(
            kind=kind,
            subject=subject,
            order=self.order,
            payment_attempt=self.attempt,
            now=self.now,
        )
        repeated = open_attention(
            kind=kind,
            subject=subject,
            order=self.order,
            payment_attempt=self.attempt,
            now=self.now + timedelta(minutes=5),
        )

        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(
            CommerceAttention.objects.filter(
                kind=kind,
                subject=subject,
                resolved_at__isnull=True,
            ).count(),
            1,
        )
        repeated.refresh_from_db()
        self.assertEqual(repeated.first_observed_at, self.now)
        self.assertEqual(repeated.last_observed_at, self.now + timedelta(minutes=5))

    def test_open_emits_a_safe_structured_log_without_customer_or_provider_secrets(self) -> None:
        """Logging email or a provider identifier would turn attention into a privacy leak."""
        logger = logging.getLogger("commerce.attention")
        handler = _CaptureHandler()
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            open_attention(
                kind="payment_mismatch",
                subject=f"payment-attempt:{self.attempt.pk}",
                order=self.order,
                payment_attempt=self.attempt,
                now=self.now,
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertEqual(len(handler.records), 1)
        payload = handler.records[0].__dict__["commerce_attention"]
        self.assertEqual(
            payload,
            {
                "kind": "payment_mismatch",
                "subject": f"payment-attempt:{self.attempt.pk}",
                "order_public_number": self.order.public_number,
                "payment_attempt_id": self.attempt.pk,
            },
        )
        rendered = f"{handler.records[0].getMessage()} {payload!r}"
        self.assertNotIn("buyer@example.test", rendered)
        self.assertNotIn("provider-secret-not-for-logs", rendered)

    def test_automatic_repair_resolves_the_matching_open_record(self) -> None:
        """A repaired failure that stays open would keep escalating a non-problem."""
        attention = open_attention(
            kind="payment_reconciliation_overdue",
            subject=f"payment-attempt:{self.attempt.pk}",
            order=self.order,
            payment_attempt=self.attempt,
            now=self.now,
        )

        resolved = resolve_attention_automatically(
            attention_id=attention.pk,
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(resolved.pk, attention.pk)
        self.assertEqual(resolved.resolved_at, self.now + timedelta(minutes=1))
        self.assertEqual(
            resolved.resolution_source,
            CommerceAttention.ResolutionSource.AUTOMATIC,
        )
        self.assertEqual(resolved.resolution_comment, "")

    def test_manual_resolution_requires_and_retains_an_operator_comment(self) -> None:
        """An unrecorded manual close would make unresolved commercial conflicts disappear."""
        attention = open_attention(
            kind="manual_payment_conflict",
            subject=f"payment-attempt:{self.attempt.pk}",
            order=self.order,
            payment_attempt=self.attempt,
            now=self.now,
        )

        with self.assertRaisesRegex(ValueError, "comment"):
            resolve_attention_manually(attention_id=attention.pk, comment="   ", now=self.now)
        resolved = resolve_attention_manually(
            attention_id=attention.pk,
            comment="Bank statement checked by operator.",
            now=self.now + timedelta(minutes=2),
        )

        self.assertEqual(resolved.resolution_source, CommerceAttention.ResolutionSource.ADMIN)
        self.assertEqual(resolved.resolution_comment, "Bank statement checked by operator.")
