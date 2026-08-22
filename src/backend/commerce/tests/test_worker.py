import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from picflow.models import Event, Photo

from commerce.attention import open_attention
from commerce.email_sender import EmailMessage, EmailSendOutcome, EmailSendResult
from commerce.models import (
    CommerceAttention,
    EmailDelivery,
    Order,
    OrderAccessGrant,
    OrderItem,
    PaymentAttempt,
)
from commerce.payment_gateway import (
    CreatedPayment,
    NormalizedPaymentStatus,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentObservation,
    PaymentRequest,
)
from commerce.payments import PaymentTransitionRejected, reconcile_payment_attempt
from commerce.worker import (
    CommerceWorker,
    claim_due_payment_reconciliations,
    commerce_worker_health,
)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _WorkerSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.timeouts: list[int] = []
        self.in_atomic_blocks: list[bool] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        self.timeouts.append(timeout_seconds)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        return EmailSendResult(outcome=EmailSendOutcome.SUCCEEDED)


class _RetryableWorkerSender(_WorkerSender):
    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        self.timeouts.append(timeout_seconds)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        return EmailSendResult(
            outcome=EmailSendOutcome.RETRYABLE_FAILURE,
            safe_failure_category="test_timeout",
        )


class _LeaseInspectingWorkerSender(_WorkerSender):
    def __init__(self) -> None:
        super().__init__()
        self.lease_expiries: list[datetime] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.lease_expiries.extend(
            EmailDelivery.objects.filter(state=EmailDelivery.State.PROCESSING).values_list(
                "lease_expires_at", flat=True
            )
        )
        return super().send(message, timeout_seconds=timeout_seconds)


class _TimeoutThenSuccessWorkerSender(_WorkerSender):
    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        self.timeouts.append(timeout_seconds)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        if len(self.messages) == 1:
            raise TimeoutError("deterministic timeout")
        return EmailSendResult(outcome=EmailSendOutcome.SUCCEEDED)


class _ObservationGateway:
    adapter_key = "deterministic-test"

    def __init__(self, observation: PaymentObservation) -> None:
        self.observation = observation
        self.fetches: list[str] = []
        self.in_atomic_blocks: list[bool] = []

    def create_payment(self, request: PaymentRequest) -> CreatedPayment:  # pragma: no cover
        raise AssertionError("The worker must reconcile, not create payments.")

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        self.fetches.append(provider_payment_id)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        return self.observation

    def authenticate_notification(self, notification):  # pragma: no cover
        raise AssertionError("The worker must not consume provider callbacks.")


class _UnavailableGateway(_ObservationGateway):
    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        self.fetches.append(provider_payment_id)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        raise PaymentGatewayError(PaymentGatewayErrorCategory.UNAVAILABLE)


class _LeaseExpiringGateway(_ObservationGateway):
    def __init__(
        self,
        observation: PaymentObservation,
        *,
        attempt_id: int,
        expired_at: datetime,
    ) -> None:
        super().__init__(observation)
        self._attempt_id = attempt_id
        self._expired_at = expired_at

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        self.fetches.append(provider_payment_id)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        PaymentAttempt.objects.filter(pk=self._attempt_id).update(
            reconciliation_lease_expires_at=self._expired_at,
        )
        return self.observation


class CommerceWorkerTests(TransactionTestCase):
    """The breaks caught here would duplicate fulfillment or disclose commercial secrets in jobs."""

    signing_secret = "dedicated-order-access-signing-secret"

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
        self.event = Event.objects.create(
            name="Worker event",
            slug="commerce-worker-event",
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 22),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="worker-photo",
            event=self.event,
            src="photos/worker.jpg",
        )

    def make_order(self, *, public_number: str, status: object = Order.Status.PAID) -> Order:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS commerce_order_insert_total_guard, "
                    "commerce_order_item_total_guard DEFERRED"
                )
            order = Order.objects.create(
                public_number=public_number,
                event=self.event,
                purchase_browser_token_sha256="a" * 64,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
                status=str(status),
                paid_at=self.now if str(status) == str(Order.Status.PAID) else None,
            )
            OrderItem.objects.create(
                order=order,
                photo=self.photo,
                photo_public_id=self.photo.pk,
                unit_price_kopecks=30000,
                line_total_kopecks=30000,
            )
        return order

    def make_delivery(self, order: Order) -> EmailDelivery:
        grant = OrderAccessGrant.objects.create(
            order=order,
            source=OrderAccessGrant.Source.CHECKOUT,
        )
        return EmailDelivery.objects.create(
            order=order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=order.delivery_email,
            access_grant=grant,
            next_attempt_at=self.now,
        )

    def make_due_payment_attempt(self, order: Order, *, suffix: str) -> PaymentAttempt:
        return PaymentAttempt.objects.create(
            order=order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="deterministic-test",
            idempotency_key=f"reconciliation-{suffix}",
            provider_payment_id=f"provider-{suffix}",
            expires_at=self.now - timedelta(seconds=1),
            reconciliation_next_attempt_at=self.now - timedelta(seconds=1),
        )

    def make_worker(
        self,
        *,
        sender: _WorkerSender,
        gateway: _ObservationGateway,
        claim_limit: int = 1,
        email_timeout_seconds: int = 17,
    ) -> CommerceWorker:
        return CommerceWorker(
            email_sender=sender,
            payment_gateway=gateway,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=lambda grant, signature: (
                f"https://findme.example.test/orders/{grant.order_id}/{grant.pk}/{signature}"
            ),
            support_contact="support@example.test",
            admin_url_for_attention=lambda attention: (
                f"https://findme.example.test/admin/commerce/commerceattention/{attention.pk}/change/"
            ),
            claim_limit=claim_limit,
            email_timeout_seconds=email_timeout_seconds,
        )

    def test_claims_only_a_bounded_delivery_batch_and_logs_only_safe_work_references(self) -> None:
        """An unbounded batch or customer-bearing log would stall or disclose fulfillment work."""
        order = self.make_order(public_number="FM-WRKR2222")
        first = self.make_delivery(order)
        second = self.make_delivery(order)
        observation = PaymentObservation(
            provider_payment_id="provider-safe-1",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="worker-unused",
        )
        sender = _WorkerSender()
        worker = self.make_worker(sender=sender, gateway=_ObservationGateway(observation))
        logger = logging.getLogger("commerce.worker")
        handler = _CaptureHandler()
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            result = worker.run_once(now=self.now)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(result.email_deliveries, 1)
        self.assertEqual(result.payment_reconciliations, 0)
        self.assertEqual(first.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(second.state, EmailDelivery.State.PENDING)
        self.assertEqual(sender.timeouts, [17])
        self.assertEqual(sender.in_atomic_blocks, [False])
        rendered = " ".join(
            f"{record.getMessage()} {record.__dict__.get('commerce_worker', {})!r}"
            for record in handler.records
        )
        self.assertIn("email_delivery", rendered)
        self.assertNotIn("buyer@example.test", rendered)
        self.assertNotIn(self.signing_secret, rendered)

    def test_email_batch_lease_covers_its_bounded_timeout_budget(self) -> None:
        """Later messages in a finite batch must not begin with an already doomed lease."""
        order = self.make_order(public_number="FM-ELSE2222")
        self.make_delivery(order)
        self.make_delivery(order)
        observation = PaymentObservation(
            provider_payment_id="provider-email-lease",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="unused-email-lease",
        )
        sender = _LeaseInspectingWorkerSender()
        worker = self.make_worker(
            sender=sender,
            gateway=_ObservationGateway(observation),
            claim_limit=2,
            email_timeout_seconds=180,
        )

        result = worker.run_once(now=self.now)

        self.assertEqual(result.email_deliveries, 2)
        self.assertEqual(len(sender.lease_expiries), 3)
        self.assertGreaterEqual(
            min(sender.lease_expiries),
            self.now + timedelta(minutes=7),
        )

    def test_unfixed_worker_clock_uses_fresh_delivery_result_time(self) -> None:
        """A slow first send must schedule retry from its own result, not pass-start time."""
        order = self.make_order(public_number="FM-FRESH222")
        delivery = self.make_delivery(order)
        observation = PaymentObservation(
            provider_payment_id="provider-fresh-clock",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="unused-fresh-clock",
        )
        sender = _RetryableWorkerSender()
        worker = self.make_worker(sender=sender, gateway=_ObservationGateway(observation))
        sent_at = self.now + timedelta(minutes=2)

        clock_values = iter((self.now,))
        with patch(
            "commerce.delivery.timezone.now",
            side_effect=lambda: next(clock_values, sent_at),
        ):
            worker.run_once()

        delivery.refresh_from_db()
        self.assertEqual(delivery.next_attempt_at, sent_at + timedelta(minutes=1))

    def test_email_timeout_does_not_abandon_the_rest_of_a_claimed_batch(self) -> None:
        """One timed-out provider call leaves later bounded claims eligible for the same pass."""
        order = self.make_order(public_number="FM-TMXUT222")
        first = self.make_delivery(order)
        second = self.make_delivery(order)
        observation = PaymentObservation(
            provider_payment_id="provider-timeout-batch",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="unused-timeout-batch",
        )
        sender = _TimeoutThenSuccessWorkerSender()
        worker = self.make_worker(
            sender=sender,
            gateway=_ObservationGateway(observation),
            claim_limit=2,
        )

        result = worker.run_once(now=self.now)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(result.email_deliveries, 2)
        self.assertEqual(first.state, EmailDelivery.State.RETRY_WAIT)
        self.assertEqual(second.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(sender.in_atomic_blocks, [False, False])

    def test_reconciles_one_due_attempt_outside_a_transaction_and_restart_is_idempotent(
        self,
    ) -> None:
        """Retrying a completed reconciliation cannot create a second fulfillment transition."""
        order = self.make_order(public_number="FM-RCNC2222", status=Order.Status.PENDING)
        OrderAccessGrant.objects.create(order=order, source=OrderAccessGrant.Source.CHECKOUT)
        attempt = PaymentAttempt.objects.create(
            order=order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="deterministic-test",
            idempotency_key="worker-payment-attempt",
            provider_payment_id="provider-safe-2",
            expires_at=self.now - timedelta(seconds=1),
            reconciliation_next_attempt_at=self.now - timedelta(seconds=1),
        )
        observation = PaymentObservation(
            provider_payment_id=attempt.provider_payment_id,
            status=NormalizedPaymentStatus.SUCCEEDED,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key=attempt.idempotency_key,
        )
        sender = _WorkerSender()
        gateway = _ObservationGateway(observation)
        worker = self.make_worker(sender=sender, gateway=gateway)

        first = worker.run_once(now=self.now)
        second = worker.run_once(now=self.now + timedelta(minutes=1))

        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(first.payment_reconciliations, 1)
        self.assertEqual(second.payment_reconciliations, 0)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PENDING)
        self.assertIsNone(attempt.reconciliation_lease_id)
        self.assertIsNone(attempt.reconciliation_lease_expires_at)
        self.assertIsNone(attempt.reconciliation_next_attempt_at)
        self.assertEqual(gateway.fetches, ["provider-safe-2"])
        self.assertEqual(gateway.in_atomic_blocks, [False])
        self.assertEqual(EmailDelivery.objects.filter(order=order).count(), 1)

    def test_reconciliation_claim_is_exclusive_and_recovers_an_expired_worker_lease(self) -> None:
        """Two pollers and a restart must never share one provider-status fetch claim."""
        order = self.make_order(public_number="FM-RCLM2222", status=Order.Status.PENDING)
        attempt = self.make_due_payment_attempt(order, suffix="claim")

        first = claim_due_payment_reconciliations(
            now=self.now,
            limit=1,
            adapter_key="deterministic-test",
        )
        second = claim_due_payment_reconciliations(
            now=self.now,
            limit=1,
            adapter_key="deterministic-test",
        )

        attempt.refresh_from_db()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].attempt_id, attempt.pk)
        self.assertEqual(second, ())
        self.assertEqual(
            attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PROCESSING
        )
        self.assertEqual(attempt.reconciliation_lease_id, first[0].lease_id)
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            reconciliation_lease_expires_at=self.now - timedelta(seconds=1),
        )

        recovered = claim_due_payment_reconciliations(
            now=self.now + timedelta(seconds=1),
            limit=1,
            adapter_key="deterministic-test",
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].attempt_id, attempt.pk)
        self.assertNotEqual(recovered[0].lease_id, first[0].lease_id)

    def test_concurrent_reconciliation_claims_share_no_provider_status_fetch(self) -> None:
        """Two PostgreSQL pollers racing for one due attempt produce exactly one lease."""
        order = self.make_order(public_number="FM-RCNC2223", status=Order.Status.PENDING)
        attempt = self.make_due_payment_attempt(order, suffix="concurrent")

        def claim_once():
            close_old_connections()
            try:
                return claim_due_payment_reconciliations(
                    now=self.now,
                    limit=1,
                    adapter_key="deterministic-test",
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(claim_once), executor.submit(claim_once))
            claimed_batches = [future.result(timeout=2) for future in futures]

        claims = [claim for batch in claimed_batches for claim in batch]
        attempt.refresh_from_db()
        self.assertEqual([claim.attempt_id for claim in claims], [attempt.pk])
        self.assertEqual(
            attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PROCESSING
        )
        self.assertEqual(attempt.reconciliation_lease_id, claims[0].lease_id)

    def test_reconciliation_claim_limit_is_bounded(self) -> None:
        """A direct queue service call must not turn one polling pass into an unbounded claim."""
        with self.assertRaisesRegex(ValueError, "bounded"):
            claim_due_payment_reconciliations(
                now=self.now,
                limit=21,
                adapter_key="deterministic-test",
            )

    def test_unavailable_reconciliation_is_released_with_backoff_not_refetched_each_poll(
        self,
    ) -> None:
        """An unavailable provider is retried later, never hammered on every poll tick."""
        order = self.make_order(public_number="FM-RBCK2222", status=Order.Status.PENDING)
        attempt = self.make_due_payment_attempt(order, suffix="backoff")
        sender = _WorkerSender()
        observation = PaymentObservation(
            provider_payment_id="provider-unused-backoff",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="unused-backoff",
        )
        gateway = _UnavailableGateway(observation)
        worker = self.make_worker(sender=sender, gateway=gateway)

        first = worker.run_once(now=self.now)
        second = worker.run_once(now=self.now + timedelta(seconds=5))

        attempt.refresh_from_db()
        self.assertEqual(first.payment_reconciliations, 0)
        self.assertEqual(second.payment_reconciliations, 0)
        self.assertEqual(gateway.fetches, [attempt.provider_payment_id])
        self.assertEqual(attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PENDING)
        self.assertEqual(attempt.reconciliation_lease_id, None)
        self.assertGreater(attempt.reconciliation_next_attempt_at, self.now + timedelta(seconds=5))

    def test_second_unavailable_reconciliation_exhausts_and_null_due_stays_quiet(self) -> None:
        """The one retry must end without a fallback claim, fetch, or false unhealthy worker."""
        order = self.make_order(public_number="FM-REXT2222", status=Order.Status.PENDING)
        attempt = self.make_due_payment_attempt(order, suffix="exhausted")
        assert attempt.expires_at is not None
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            reconciliation_next_attempt_at=attempt.expires_at,
        )
        sender = _WorkerSender()
        observation = PaymentObservation(
            provider_payment_id="provider-unused-exhausted",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="unused-exhausted",
        )
        gateway = _UnavailableGateway(observation)
        worker = self.make_worker(sender=sender, gateway=gateway)

        first = worker.run_once(now=self.now)
        attempt.refresh_from_db()
        retry_at = attempt.reconciliation_next_attempt_at
        self.assertEqual(first.payment_reconciliations, 0)
        self.assertEqual(retry_at, self.now + timedelta(minutes=5))

        second = worker.run_once(now=retry_at)
        third = worker.run_once(now=retry_at + timedelta(minutes=5))

        attempt.refresh_from_db()
        health = commerce_worker_health(
            now=retry_at + timedelta(minutes=5),
            max_ready_age=timedelta(0),
            worker_is_alive=lambda: True,
        )
        self.assertEqual(second.payment_reconciliations, 0)
        self.assertEqual(third.payment_reconciliations, 0)
        self.assertEqual(
            gateway.fetches,
            [attempt.provider_payment_id, attempt.provider_payment_id],
        )
        self.assertEqual(attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PENDING)
        self.assertIsNone(attempt.reconciliation_lease_id)
        self.assertIsNone(attempt.reconciliation_next_attempt_at)
        self.assertEqual(
            CommerceAttention.objects.filter(
                kind=CommerceAttention.Kind.PAYMENT_RECONCILIATION_OVERDUE,
                subject=f"payment-attempt:{attempt.pk}",
                resolved_at__isnull=True,
            ).count(),
            1,
        )
        self.assertTrue(health.healthy)
        self.assertIsNone(health.oldest_ready_work_type)
        self.assertIsNone(health.oldest_ready_age)

    def test_reconciliation_completion_is_fenced_and_an_expired_restart_claim_can_finish_once(
        self,
    ) -> None:
        """A late status response cannot settle payment after its worker lease has expired."""
        order = self.make_order(public_number="FM-RFNC2222", status=Order.Status.PENDING)
        OrderAccessGrant.objects.create(order=order, source=OrderAccessGrant.Source.CHECKOUT)
        attempt = self.make_due_payment_attempt(order, suffix="fence")
        observation = PaymentObservation(
            provider_payment_id=attempt.provider_payment_id,
            status=NormalizedPaymentStatus.SUCCEEDED,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key=attempt.idempotency_key,
        )
        expired_gateway = _LeaseExpiringGateway(
            observation,
            attempt_id=attempt.pk,
            expired_at=self.now - timedelta(seconds=1),
        )
        sender = _WorkerSender()

        late = self.make_worker(sender=sender, gateway=expired_gateway).run_once(now=self.now)

        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(late.payment_reconciliations, 0)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(
            attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PROCESSING
        )
        self.assertEqual(EmailDelivery.objects.filter(order=order).count(), 0)

        recovered_gateway = _ObservationGateway(observation)
        recovered = self.make_worker(sender=sender, gateway=recovered_gateway).run_once(
            now=self.now + timedelta(seconds=1)
        )

        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(recovered.payment_reconciliations, 1)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(recovered_gateway.fetches, [attempt.provider_payment_id])

    def test_reconciliation_rechecks_the_clock_after_provider_io_before_settling(self) -> None:
        """A fetch that crosses its lease deadline cannot apply its now-stale response."""
        order = self.make_order(public_number="FM-RCLK2222", status=Order.Status.PENDING)
        OrderAccessGrant.objects.create(order=order, source=OrderAccessGrant.Source.CHECKOUT)
        attempt = self.make_due_payment_attempt(order, suffix="clock")
        observation = PaymentObservation(
            provider_payment_id=attempt.provider_payment_id,
            status=NormalizedPaymentStatus.SUCCEEDED,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key=attempt.idempotency_key,
        )
        claim = claim_due_payment_reconciliations(
            now=self.now,
            limit=1,
            adapter_key="deterministic-test",
        )[0]

        completed_at = self.now + timedelta(minutes=6)
        clock_values = iter((self.now, completed_at))
        with patch(
            "commerce.payments.timezone.now",
            side_effect=lambda: next(clock_values, completed_at),
        ):
            with self.assertRaisesRegex(PaymentTransitionRejected, "no longer current"):
                reconcile_payment_attempt(
                    attempt_id=attempt.pk,
                    gateway=_ObservationGateway(observation),
                    expected_reconciliation_lease_id=claim.lease_id,
                )

        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(
            attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PROCESSING
        )
        self.assertEqual(EmailDelivery.objects.filter(order=order).count(), 0)

    def test_recovers_an_expired_delivery_lease_before_sending_once(self) -> None:
        """A worker restart must make abandoned work available without accepting its old lease."""
        order = self.make_order(public_number="FM-LSXP2222")
        delivery = self.make_delivery(order)
        EmailDelivery.objects.filter(pk=delivery.pk).update(
            state=EmailDelivery.State.PROCESSING,
            lease_id=uuid4(),
            lease_expires_at=self.now - timedelta(seconds=1),
        )
        observation = PaymentObservation(
            provider_payment_id="provider-safe-3",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="worker-unused-lease",
        )
        sender = _WorkerSender()

        result = self.make_worker(
            sender=sender,
            gateway=_ObservationGateway(observation),
        ).run_once(now=self.now)

        delivery.refresh_from_db()
        self.assertEqual(result.email_deliveries, 1)
        self.assertEqual(delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempt_count, 1)

    def test_attention_messages_go_only_to_active_responsible_staff_once_per_day(self) -> None:
        """A broad recipient query or too-frequent reminder leaks or spams operator attention."""
        order = self.make_order(public_number="FM-ATTNWRK2")
        attention = open_attention(
            kind=str(CommerceAttention.Kind.EMAIL_EXHAUSTED),
            subject="email-delivery:42",
            order=order,
            now=self.now,
        )
        content_type = ContentType.objects.get_for_model(CommerceAttention)
        permission, _created = Permission.objects.get_or_create(
            content_type=content_type,
            codename="handle_attention",
            defaults={"name": "Can handle Commerce attention"},
        )
        responsible = get_user_model().objects.create_user(
            username="responsible",
            email="operator@example.test",
            is_staff=True,
            is_active=True,
        )
        responsible.user_permissions.add(permission)
        get_user_model().objects.create_user(
            username="inactive",
            email="inactive@example.test",
            is_staff=True,
            is_active=False,
        )
        get_user_model().objects.create_user(
            username="unpermitted",
            email="unpermitted@example.test",
            is_staff=True,
            is_active=True,
        )
        observation = PaymentObservation(
            provider_payment_id="provider-safe-4",
            status=NormalizedPaymentStatus.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key="worker-unused-attention",
        )
        sender = _WorkerSender()
        worker = self.make_worker(sender=sender, gateway=_ObservationGateway(observation))

        first = worker.run_once(now=self.now)
        second = worker.run_once(now=self.now + timedelta(hours=1))
        third = worker.run_once(now=self.now + timedelta(hours=24))

        attention.refresh_from_db()
        self.assertEqual(first.attention_reminders, 1)
        self.assertEqual(second.attention_reminders, 0)
        self.assertEqual(third.attention_reminders, 1)
        self.assertEqual(
            [message.recipient_email for message in sender.messages],
            [
                "operator@example.test",
                "operator@example.test",
            ],
        )
        self.assertIn("email_exhausted", sender.messages[0].text_body)
        self.assertIn(order.public_number, sender.messages[0].text_body)
        self.assertIn("/admin/commerce/commerceattention/", sender.messages[0].text_body)
        self.assertNotIn("buyer@example.test", sender.messages[0].text_body)
        self.assertEqual(attention.next_reminder_at, self.now + timedelta(hours=48))

    def test_health_is_read_only_and_fails_for_stale_ready_work_or_dead_worker(self) -> None:
        """A mutating probe or a healthy stale queue would defeat independent monitoring."""
        order = self.make_order(public_number="FM-HEALTWK2")
        delivery = self.make_delivery(order)
        EmailDelivery.objects.filter(pk=delivery.pk).update(
            next_attempt_at=self.now - timedelta(minutes=6)
        )
        before = EmailDelivery.objects.values(
            "state", "attempt_count", "next_attempt_at", "lease_id", "lease_expires_at"
        ).get(pk=delivery.pk)

        stale = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(minutes=5),
            worker_is_alive=lambda: True,
        )
        dead = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(minutes=10),
            worker_is_alive=lambda: False,
        )

        after = EmailDelivery.objects.values(
            "state", "attempt_count", "next_attempt_at", "lease_id", "lease_expires_at"
        ).get(pk=delivery.pk)
        self.assertFalse(stale.healthy)
        self.assertEqual(stale.oldest_ready_work_type, "email_delivery")
        self.assertFalse(dead.healthy)
        self.assertFalse(dead.worker_alive)
        self.assertEqual(before, after)

    def test_health_treats_an_expired_processing_delivery_as_ready_without_mutating_it(
        self,
    ) -> None:
        """A hung sender must become visible at lease expiry even while its process stays alive."""
        order = self.make_order(public_number="FM-HUNGW222")
        delivery = self.make_delivery(order)
        lease_expired_at = self.now - timedelta(minutes=6)
        EmailDelivery.objects.filter(pk=delivery.pk).update(
            state=EmailDelivery.State.PROCESSING,
            lease_id=uuid4(),
            lease_expires_at=lease_expired_at,
        )
        before = EmailDelivery.objects.values(
            "state", "attempt_count", "next_attempt_at", "lease_id", "lease_expires_at"
        ).get(pk=delivery.pk)

        health = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(minutes=5),
            worker_is_alive=lambda: True,
        )

        after = EmailDelivery.objects.values(
            "state", "attempt_count", "next_attempt_at", "lease_id", "lease_expires_at"
        ).get(pk=delivery.pk)
        self.assertFalse(health.healthy)
        self.assertTrue(health.worker_alive)
        self.assertEqual(health.oldest_ready_work_type, "email_delivery")
        self.assertEqual(health.oldest_ready_age, timedelta(minutes=6))
        self.assertEqual(before, after)

    def test_health_reports_due_reconciliation_but_ignores_a_live_processing_lease(self) -> None:
        """Read-only health must cover both payment readiness and normal in-flight work."""
        order = self.make_order(public_number="FM-RHLT2222", status=Order.Status.PENDING)
        attempt = self.make_due_payment_attempt(order, suffix="health")
        before = PaymentAttempt.objects.values(
            "status",
            "reconciliation_state",
            "reconciliation_lease_id",
            "reconciliation_lease_expires_at",
            "reconciliation_next_attempt_at",
        ).get(pk=attempt.pk)

        due = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(0),
            worker_is_alive=lambda: True,
        )

        after_due = PaymentAttempt.objects.values(
            "status",
            "reconciliation_state",
            "reconciliation_lease_id",
            "reconciliation_lease_expires_at",
            "reconciliation_next_attempt_at",
        ).get(pk=attempt.pk)
        self.assertFalse(due.healthy)
        self.assertEqual(due.oldest_ready_work_type, "payment_reconciliation")
        self.assertEqual(before, after_due)

        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            reconciliation_state=PaymentAttempt.ReconciliationState.PROCESSING,
            reconciliation_lease_id=uuid4(),
            reconciliation_lease_expires_at=self.now + timedelta(minutes=5),
        )
        live = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(0),
            worker_is_alive=lambda: True,
        )

        self.assertTrue(live.healthy)
        self.assertIsNone(live.oldest_ready_work_type)

    def test_management_commands_fail_closed_without_task10_runtime_wiring(self) -> None:
        """A dark deployment cannot start a test sender or report a dead worker healthy."""
        with self.assertRaisesRegex(CommandError, "COMMERCE_WORKER_FACTORY"):
            call_command("run_commerce_worker", "--once")
        with self.assertRaisesRegex(CommandError, "worker is not live"):
            call_command("commerce_worker_health", "--max-ready-age-seconds", "300")
