from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from threading import Event as ThreadEvent
from time import monotonic, sleep
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from picflow.models import Event, Photo

from commerce.attention import open_attention
from commerce.capabilities import revoke_order_access_grant
from commerce.delivery import (
    ResendOrderAccessRateLimited,
    claim_due_email_deliveries,
    correct_delivery_email,
    resend_order_access,
    retry_failed_email_delivery,
    send_claimed_email_delivery,
)
from commerce.email_sender import EmailMessage, EmailSendOutcome, EmailSendResult
from commerce.models import (
    CommerceAttention,
    EmailDelivery,
    EmailDeliveryAttempt,
    Order,
    OrderAccessGrant,
    OrderItem,
)
from commerce.worker import commerce_worker_health


class _RecordingSender:
    def __init__(self, outcomes: tuple[EmailSendOutcome, ...]) -> None:
        self._outcomes = deque(outcomes)
        self.messages: list[EmailMessage] = []
        self.in_atomic_blocks: list[bool] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        outcome = self._outcomes.popleft()
        if outcome == EmailSendOutcome.SUCCEEDED:
            return EmailSendResult(outcome=outcome)
        return EmailSendResult(outcome=outcome, safe_failure_category="test_provider_failure")


class _TimeoutSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.in_atomic_blocks: list[bool] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        self.in_atomic_blocks.append(connection.in_atomic_block)
        raise TimeoutError("provider did not answer")


class _CorrectingSender:
    def __init__(
        self,
        *,
        order_id: int,
        corrected_email: str,
        now: datetime,
        outcome: EmailSendOutcome = EmailSendOutcome.SUCCEEDED,
    ) -> None:
        self._order_id = order_id
        self._corrected_email = corrected_email
        self._now = now
        self._outcome = outcome
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        correct_delivery_email(
            order_id=self._order_id,
            delivery_email=self._corrected_email,
            now=self._now,
        )
        if self._outcome == EmailSendOutcome.SUCCEEDED:
            return EmailSendResult(outcome=self._outcome)
        return EmailSendResult(
            outcome=self._outcome,
            safe_failure_category="test_provider_failure",
        )


class _RevokingSender:
    """Model a grant being revoked while the provider call is already in flight."""

    def __init__(self, *, grant: OrderAccessGrant, now: datetime) -> None:
        self._grant = grant
        self._now = now
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:  # noqa: ARG002
        self.messages.append(message)
        revoke_order_access_grant(self._grant, revoked_at=self._now)
        return EmailSendResult(outcome=EmailSendOutcome.SUCCEEDED)


class _LeaseExpiringSender:
    def __init__(self, *, delivery_id: int, expired_at: datetime) -> None:
        self._delivery_id = delivery_id
        self._expired_at = expired_at
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage, *, timeout_seconds: int) -> EmailSendResult:
        self.messages.append(message)
        EmailDelivery.objects.filter(pk=self._delivery_id).update(
            lease_expires_at=self._expired_at,
        )
        return EmailSendResult(outcome=EmailSendOutcome.SUCCEEDED)


class _AccessUrlBuilder:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, grant: OrderAccessGrant, signature: str) -> str:
        url = f"https://findme.example.test/orders/access/{grant.pk}/{signature}"
        self.urls.append(url)
        return url


def _row_share_locks_for_backend(*, backend_pid: int) -> set[str]:
    """Return Commerce row-lock targets for a separate PostgreSQL backend."""
    tables = (
        EmailDelivery._meta.db_table,
        Order._meta.db_table,
        OrderAccessGrant._meta.db_table,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_class.relname
            FROM pg_locks
            INNER JOIN pg_class ON pg_class.oid = pg_locks.relation
            WHERE pg_locks.pid = %s
              AND pg_locks.mode = 'RowShareLock'
              AND pg_class.relname IN (%s, %s, %s)
            """,
            [backend_pid, *tables],
        )
        return {row[0] for row in cursor.fetchall()}


def _backend_is_waiting_for_lock(*, backend_pid: int, timeout_seconds: float = 5) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                [backend_pid],
            )
            row = cursor.fetchone()
        if row is not None and row[0] == "Lock":
            return True
        sleep(0.01)
    return False


class EmailDeliveryServiceTests(TransactionTestCase):
    """The breaks caught here would lose paid access or send it to an obsolete address."""

    signing_secret = "dedicated-order-access-signing-secret"
    support_contact = "support@example.test"

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
        self.event = Event.objects.create(
            name="Night Ride",
            slug="night-ride-delivery",
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 22),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="delivery-photo",
            event=self.event,
            src="photos/delivery.jpg",
        )
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS commerce_order_insert_total_guard, "
                    "commerce_order_item_total_guard DEFERRED"
                )
            self.order = Order.objects.create(
                public_number="FM-DELVERY2",
                event=self.event,
                purchase_browser_token_sha256="a" * 64,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
                status=Order.Status.PAID,
                paid_at=self.now,
            )
            OrderItem.objects.create(
                order=self.order,
                photo=self.photo,
                photo_public_id=self.photo.pk,
                unit_price_kopecks=30000,
                line_total_kopecks=30000,
            )
        self.grant = OrderAccessGrant.objects.create(
            order=self.order,
            source=OrderAccessGrant.Source.CHECKOUT,
        )

    def create_delivery(self, *, next_attempt_at: datetime | None = None) -> EmailDelivery:
        return EmailDelivery.objects.create(
            order=self.order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=self.order.delivery_email,
            access_grant=self.grant,
            next_attempt_at=next_attempt_at or self.now,
        )

    def send_one(
        self,
        *,
        delivery: EmailDelivery,
        now: datetime,
        sender: _RecordingSender,
        url_builder: _AccessUrlBuilder,
    ) -> None:
        claims = claim_due_email_deliveries(now=now, limit=1)
        self.assertEqual([claim.delivery_id for claim in claims], [delivery.pk])
        send_claimed_email_delivery(
            claim=claims[0],
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=url_builder,
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=now,
        )

    def test_sends_the_exact_plaintext_customer_message_with_one_newly_reconstructed_link(
        self,
    ) -> None:
        """A stored URL, preview, tracking pixel, or altered payment copy leaks or misleads."""
        delivery = self.create_delivery()
        sender = _RecordingSender((EmailSendOutcome.SUCCEEDED,))
        url_builder = _AccessUrlBuilder()

        claims = claim_due_email_deliveries(now=self.now, limit=1)
        self.assertEqual(url_builder.urls, [])
        send_claimed_email_delivery(
            claim=claims[0],
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=url_builder,
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )

        self.order.refresh_from_db()
        delivery.refresh_from_db()
        message = sender.messages[0]
        order_date = self.order.created_at.strftime("%d.%m.%Y")
        self.assertEqual(message.recipient_email, "buyer@example.test")
        self.assertEqual(message.subject, "Ваши фотографии с мероприятия «Night Ride»")
        self.assertEqual(
            message.text_body,
            "\n".join(
                (
                    "Здравствуйте!",
                    "",
                    f"Заказ {self.order.public_number} от {order_date} оплачен.",
                    "Фотографий: 1",
                    "Итого: 300 ₽",
                    "",
                    "Открыть оригиналы:",
                    url_builder.urls[0],
                    "Не пересылайте эту секретную ссылку: по ней можно открыть оригиналы.",
                    "",
                    "Поддержка: support@example.test",
                )
            ),
        )
        self.assertEqual(message.text_body.count("https://"), 1)
        self.assertNotIn("preview", message.text_body.casefold())
        self.assertNotIn("utm_", message.text_body)
        self.assertNotIn("attachment", message.text_body.casefold())
        self.assertEqual(delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(sender.in_atomic_blocks, [False])
        self.assertTrue(
            {"text_body", "signed_url", "signature", "access_url"}.isdisjoint(
                {field.name for field in EmailDelivery._meta.local_fields}
            )
        )

    def test_retryable_failures_follow_the_bounded_delivery_schedule_and_exhaust_once(self) -> None:
        """An unbounded or wrongly delayed retry could silently lose a paid order's access email."""
        delivery = self.create_delivery()
        sender = _RecordingSender((EmailSendOutcome.RETRYABLE_FAILURE,) * 6)
        url_builder = _AccessUrlBuilder()
        attempt_times = [self.now]
        retry_delays = [
            timedelta(minutes=1),
            timedelta(minutes=5),
            timedelta(minutes=30),
            timedelta(hours=2),
            timedelta(hours=12),
        ]

        for attempt_number in range(1, 7):
            self.send_one(
                delivery=delivery,
                now=attempt_times[-1],
                sender=sender,
                url_builder=url_builder,
            )
            delivery.refresh_from_db()
            if attempt_number < 6:
                self.assertEqual(delivery.state, EmailDelivery.State.RETRY_WAIT)
                self.assertEqual(
                    delivery.next_attempt_at,
                    attempt_times[-1] + retry_delays[attempt_number - 1],
                )
                attempt_times.append(delivery.next_attempt_at)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, EmailDelivery.State.FAILED)
        self.assertEqual(delivery.attempt_count, 6)
        self.assertLess(attempt_times[-1] - self.now, timedelta(hours=24))
        self.assertEqual(EmailDeliveryAttempt.objects.filter(delivery=delivery).count(), 6)
        self.assertEqual(
            CommerceAttention.objects.filter(
                kind=CommerceAttention.Kind.EMAIL_EXHAUSTED,
                subject=f"email-delivery:{delivery.pk}",
                resolved_at__isnull=True,
            ).count(),
            1,
        )

    def test_terminal_delivery_failure_stops_immediately_and_opens_attention(self) -> None:
        """Retrying a terminal provider rejection can never repair the same recipient address."""
        delivery = self.create_delivery()
        sender = _RecordingSender((EmailSendOutcome.TERMINAL_FAILURE,))
        url_builder = _AccessUrlBuilder()

        self.send_one(
            delivery=delivery,
            now=self.now,
            sender=sender,
            url_builder=url_builder,
        )

        delivery.refresh_from_db()
        attempt = EmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(delivery.state, EmailDelivery.State.FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(attempt.outcome, EmailDeliveryAttempt.Outcome.TERMINAL_FAILURE)
        self.assertEqual(
            CommerceAttention.objects.filter(
                kind=CommerceAttention.Kind.EMAIL_EXHAUSTED,
                subject=f"email-delivery:{delivery.pk}",
                resolved_at__isnull=True,
            ).count(),
            1,
        )

    def test_expired_claim_is_never_sent_or_allowed_to_record_a_late_result(self) -> None:
        """A recovered lease must fence a late worker before it can disclose an old link."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(
            now=self.now,
            limit=1,
            lease_duration=timedelta(seconds=1),
        )[0]
        sender = _RecordingSender((EmailSendOutcome.SUCCEEDED,))

        completed = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=_AccessUrlBuilder(),
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now + timedelta(seconds=1),
        )

        delivery.refresh_from_db()
        self.assertIsNone(completed)
        self.assertEqual(sender.messages, [])
        self.assertEqual(delivery.state, EmailDelivery.State.PROCESSING)
        self.assertEqual(EmailDeliveryAttempt.objects.filter(delivery=delivery).count(), 0)

    def test_expired_lease_recovery_is_bounded_to_the_claim_batch(self) -> None:
        """A large abandoned backlog must not make one worker pass rewrite every delivery."""
        deliveries = [self.create_delivery() for _ in range(3)]
        EmailDelivery.objects.filter(pk__in=[delivery.pk for delivery in deliveries]).update(
            state=EmailDelivery.State.PROCESSING,
            lease_id="12345678-1234-5678-1234-567812345678",
            lease_expires_at=self.now - timedelta(seconds=1),
        )

        claims = claim_due_email_deliveries(now=self.now, limit=1)

        self.assertEqual(len(claims), 1)
        self.assertEqual(
            EmailDelivery.objects.filter(state=EmailDelivery.State.PROCESSING).count(),
            3,
        )

    def test_expiry_during_provider_io_cannot_commit_a_late_delivery_result(self) -> None:
        """The result fence must reject a provider response after another worker may recover it."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        sender = _LeaseExpiringSender(
            delivery_id=delivery.pk,
            expired_at=self.now - timedelta(seconds=1),
        )

        completed = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=_AccessUrlBuilder(),
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )

        delivery.refresh_from_db()
        self.assertEqual(len(sender.messages), 1)
        self.assertIsNone(completed)
        self.assertEqual(delivery.state, EmailDelivery.State.PROCESSING)
        self.assertEqual(EmailDeliveryAttempt.objects.filter(delivery=delivery).count(), 0)

    def test_adapter_timeout_becomes_a_safe_retry_and_does_not_leave_the_claim_stuck(self) -> None:
        """A timeout must be retryable without aborting the rest of a bounded worker batch."""
        delivery = self.create_delivery()
        sender = _TimeoutSender()

        self.send_one(
            delivery=delivery,
            now=self.now,
            sender=sender,  # type: ignore[arg-type]
            url_builder=_AccessUrlBuilder(),
        )

        delivery.refresh_from_db()
        attempt = EmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(sender.in_atomic_blocks, [False])
        self.assertEqual(delivery.state, EmailDelivery.State.RETRY_WAIT)
        self.assertEqual(delivery.next_attempt_at, self.now + timedelta(minutes=1))
        self.assertEqual(attempt.safe_failure_category, "timeout")

    def test_correction_cancels_unsent_old_recipient_and_resend_uses_a_new_current_grant(
        self,
    ) -> None:
        """Reusing an old recipient or silently revoking working links loses fulfillment access."""
        old_delivery = self.create_delivery()

        corrected = correct_delivery_email(
            order_id=self.order.pk,
            delivery_email="corrected@example.test",
            now=self.now,
        )
        resent = resend_order_access(order_id=self.order.pk, now=self.now + timedelta(seconds=1))

        self.order.refresh_from_db()
        old_delivery.refresh_from_db()
        self.assertEqual(corrected.pk, self.order.pk)
        self.assertEqual(self.order.checkout_email, "buyer@example.test")
        self.assertEqual(self.order.delivery_email, "corrected@example.test")
        self.assertEqual(old_delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(old_delivery.recipient_email, "buyer@example.test")
        self.assertEqual(resent.recipient_email, "corrected@example.test")
        self.assertNotEqual(resent.access_grant_id, self.grant.pk)
        self.assertEqual(resent.access_grant.source, OrderAccessGrant.Source.RESEND)
        self.grant.refresh_from_db()
        self.assertIsNone(self.grant.revoked_at)

    def test_correction_preserves_inflight_send_and_resend_uses_current_email(
        self,
    ) -> None:
        """Correction racing provider I/O must retain the old attempt and never retry it."""
        old_delivery = self.create_delivery()
        sender = _CorrectingSender(
            order_id=self.order.pk,
            corrected_email="corrected@example.test",
            now=self.now,
        )
        url_builder = _AccessUrlBuilder()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]

        send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=url_builder,
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )
        resent = resend_order_access(order_id=self.order.pk, now=self.now + timedelta(seconds=1))

        old_delivery.refresh_from_db()
        self.order.refresh_from_db()
        old_attempt = EmailDeliveryAttempt.objects.get(delivery=old_delivery)
        self.assertEqual(self.order.delivery_email, "corrected@example.test")
        self.assertEqual(old_delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(old_attempt.recipient_email, "buyer@example.test")
        self.assertEqual(old_attempt.outcome, EmailDeliveryAttempt.Outcome.SUCCEEDED)
        self.assertEqual(resent.recipient_email, "corrected@example.test")
        self.assertNotEqual(resent.access_grant_id, old_delivery.access_grant_id)
        self.assertEqual(
            [
                claim.delivery_id
                for claim in claim_due_email_deliveries(now=self.now + timedelta(seconds=1))
            ],
            [resent.pk],
        )

    def test_successful_resend_resolves_only_the_repaired_email_failure_attention(self) -> None:
        """A confirmed resend closes its failed predecessor, not unrelated problems."""
        failed_delivery = self.create_delivery()
        self.send_one(
            delivery=failed_delivery,
            now=self.now,
            sender=_RecordingSender((EmailSendOutcome.TERMINAL_FAILURE,)),
            url_builder=_AccessUrlBuilder(),
        )
        unrelated = open_attention(
            kind=str(CommerceAttention.Kind.EMAIL_EXHAUSTED),
            subject="email-delivery:999",
            order=self.order,
            now=self.now,
        )
        resent = resend_order_access(order_id=self.order.pk, now=self.now + timedelta(seconds=1))

        self.send_one(
            delivery=resent,
            now=self.now + timedelta(seconds=1),
            sender=_RecordingSender((EmailSendOutcome.SUCCEEDED,)),
            url_builder=_AccessUrlBuilder(),
        )

        repaired = CommerceAttention.objects.get(
            kind=CommerceAttention.Kind.EMAIL_EXHAUSTED,
            subject=f"email-delivery:{failed_delivery.pk}",
        )
        unrelated.refresh_from_db()
        self.assertIsNotNone(repaired.resolved_at)
        self.assertIsNone(unrelated.resolved_at)

    def test_concurrent_resends_create_one_delivery_under_the_order_lock(self) -> None:
        start = Barrier(2)

        def resend_from_separate_connection() -> str:
            close_old_connections()
            start.wait(timeout=2)
            try:
                resend_order_access(order_id=self.order.pk)
            except ResendOrderAccessRateLimited:
                return "rate_limited"
            finally:
                close_old_connections()
            return "created"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(resend_from_separate_connection) for _ in range(2))
            outcomes = tuple(future.result(timeout=5) for future in futures)

        self.assertEqual(sorted(outcomes), ["created", "rate_limited"])
        self.assertEqual(
            EmailDelivery.objects.filter(
                order=self.order,
                access_grant__source=OrderAccessGrant.Source.RESEND,
            ).count(),
            1,
        )

    def test_correction_cancels_a_retryable_inflight_old_recipient_without_retrying_it(
        self,
    ) -> None:
        """An obsolete address may retain its audit attempt but cannot receive a later retry."""
        old_delivery = self.create_delivery()
        sender = _CorrectingSender(
            order_id=self.order.pk,
            corrected_email="corrected@example.test",
            now=self.now,
            outcome=EmailSendOutcome.RETRYABLE_FAILURE,
        )
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]

        send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=_AccessUrlBuilder(),
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )

        old_delivery.refresh_from_db()
        attempt = EmailDeliveryAttempt.objects.get(delivery=old_delivery)
        self.assertEqual(old_delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(attempt.recipient_email, "buyer@example.test")
        self.assertEqual(
            claim_due_email_deliveries(now=self.now + timedelta(days=1)),
            (),
        )

    def test_correction_before_external_io_cancels_the_old_claim_without_sending(self) -> None:
        """A worker that sees a corrected snapshot before I/O must cancel its obsolete claim."""
        old_delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        correct_delivery_email(
            order_id=self.order.pk,
            delivery_email="corrected@example.test",
            now=self.now,
        )
        sender = _RecordingSender((EmailSendOutcome.SUCCEEDED,))

        completed = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=_AccessUrlBuilder(),
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )

        old_delivery.refresh_from_db()
        self.assertIsNone(completed)
        self.assertEqual(sender.messages, [])
        self.assertEqual(old_delivery.state, EmailDelivery.State.CANCELED)

    def test_successful_current_recipient_resend_resolves_a_failure_canceled_by_correction(
        self,
    ) -> None:
        """Correcting an exhausted recipient cannot leave its repaired attention open forever."""
        failed_delivery = self.create_delivery()
        self.send_one(
            delivery=failed_delivery,
            now=self.now,
            sender=_RecordingSender((EmailSendOutcome.TERMINAL_FAILURE,)),
            url_builder=_AccessUrlBuilder(),
        )
        correct_delivery_email(
            order_id=self.order.pk,
            delivery_email="corrected@example.test",
            now=self.now + timedelta(seconds=1),
        )
        resent = resend_order_access(order_id=self.order.pk, now=self.now + timedelta(seconds=2))

        self.send_one(
            delivery=resent,
            now=self.now + timedelta(seconds=2),
            sender=_RecordingSender((EmailSendOutcome.SUCCEEDED,)),
            url_builder=_AccessUrlBuilder(),
        )

        repaired = CommerceAttention.objects.get(
            kind=CommerceAttention.Kind.EMAIL_EXHAUSTED,
            subject=f"email-delivery:{failed_delivery.pk}",
        )
        self.assertIsNotNone(repaired.resolved_at)

    def test_manual_failed_delivery_retry_reuses_one_job_and_resolves_its_attention(self) -> None:
        """A deliberate retry must not duplicate customer email or strand its original alert."""
        failed_delivery = self.create_delivery()
        self.send_one(
            delivery=failed_delivery,
            now=self.now,
            sender=_RecordingSender((EmailSendOutcome.TERMINAL_FAILURE,)),
            url_builder=_AccessUrlBuilder(),
        )

        first_retry = retry_failed_email_delivery(
            delivery_id=failed_delivery.pk,
            now=self.now + timedelta(seconds=1),
        )
        repeated_retry = retry_failed_email_delivery(
            delivery_id=failed_delivery.pk,
            now=self.now + timedelta(seconds=2),
        )

        if first_retry is None:
            raise AssertionError("The first manual retry must requeue the failed delivery.")
        self.assertEqual(first_retry.pk, failed_delivery.pk)
        self.assertIsNone(repeated_retry)
        failed_delivery.refresh_from_db()
        self.assertEqual(failed_delivery.state, EmailDelivery.State.PENDING)
        self.assertEqual(failed_delivery.attempt_count, 1)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.send_one(
            delivery=failed_delivery,
            now=self.now + timedelta(seconds=2),
            sender=_RecordingSender((EmailSendOutcome.SUCCEEDED,)),
            url_builder=_AccessUrlBuilder(),
        )
        repaired = CommerceAttention.objects.get(
            kind=CommerceAttention.Kind.EMAIL_EXHAUSTED,
            subject=f"email-delivery:{failed_delivery.pk}",
        )
        self.assertIsNotNone(repaired.resolved_at)

    def test_concurrent_failed_delivery_retries_create_only_one_current_job(self) -> None:
        """Two Admin submits for one failed delivery must serialize on its Order and grant."""
        failed_delivery = self.create_delivery()
        failed_delivery.state = EmailDelivery.State.FAILED
        failed_delivery.attempt_count = 6
        failed_delivery.save(update_fields=["state", "attempt_count"])
        start = Barrier(2)

        def retry_from_separate_connection() -> bool:
            close_old_connections()
            start.wait(timeout=2)
            try:
                return retry_failed_email_delivery(delivery_id=failed_delivery.pk) is not None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                future.result(timeout=5)
                for future in (
                    executor.submit(retry_from_separate_connection),
                    executor.submit(retry_from_separate_connection),
                )
            )

        self.assertEqual(sorted(results), [False, True])
        failed_delivery.refresh_from_db()
        self.assertEqual(failed_delivery.state, EmailDelivery.State.PENDING)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)

    def test_retry_rechecks_corrected_or_revoked_delivery_before_queueing_work(self) -> None:
        """Correction and revocation must prevent a failed old link from becoming sendable again."""
        corrected = self.create_delivery()
        corrected.state = EmailDelivery.State.FAILED
        corrected.save(update_fields=["state"])
        correct_delivery_email(
            order_id=self.order.pk,
            delivery_email="corrected@example.test",
            now=self.now,
        )
        self.assertIsNone(retry_failed_email_delivery(delivery_id=corrected.pk, now=self.now))
        corrected.refresh_from_db()
        self.assertEqual(corrected.state, EmailDelivery.State.CANCELED)
        self.order.refresh_from_db()

        revoked = EmailDelivery.objects.create(
            order=self.order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=self.order.delivery_email,
            access_grant=self.grant,
            state=EmailDelivery.State.FAILED,
            next_attempt_at=self.now,
        )
        revoked_pending = EmailDelivery.objects.create(
            order=self.order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=self.order.delivery_email,
            access_grant=self.grant,
            state=EmailDelivery.State.PENDING,
            next_attempt_at=self.now,
        )
        revoked_retry_wait = EmailDelivery.objects.create(
            order=self.order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=self.order.delivery_email,
            access_grant=self.grant,
            state=EmailDelivery.State.RETRY_WAIT,
            next_attempt_at=self.now,
        )
        revoke_order_access_grant(self.grant)
        self.assertIsNone(retry_failed_email_delivery(delivery_id=revoked.pk, now=self.now))
        for delivery in (revoked, revoked_pending, revoked_retry_wait):
            delivery.refresh_from_db()
            self.assertEqual(delivery.state, EmailDelivery.State.CANCELED)
            self.assertIsNone(delivery.lease_id)
            self.assertIsNone(delivery.lease_expires_at)

        race_grant = OrderAccessGrant.objects.create(
            order=self.order,
            source=OrderAccessGrant.Source.RESEND,
        )
        queued_before_revocation = EmailDelivery.objects.create(
            order=self.order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=self.order.delivery_email,
            access_grant=race_grant,
            state=EmailDelivery.State.FAILED,
            next_attempt_at=self.now,
        )
        queued_retry = retry_failed_email_delivery(
            delivery_id=queued_before_revocation.pk,
            now=self.now,
        )
        if queued_retry is None:
            raise AssertionError("The active failed delivery must enter the retry queue.")
        revoke_order_access_grant(race_grant)
        queued_before_revocation.refresh_from_db()
        self.assertEqual(queued_before_revocation.state, EmailDelivery.State.CANCELED)
        self.assertEqual(claim_due_email_deliveries(now=self.now), ())
        health = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(0),
            worker_is_alive=lambda: True,
        )
        self.assertTrue(health.healthy)
        self.assertIsNone(health.oldest_ready_work_type)

    def test_revocation_after_claim_before_external_io_cancels_without_sending(self) -> None:
        """A claim revoked before its provider call must terminalize without issuing a bearer."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        revoke_order_access_grant(self.grant, revoked_at=self.now)
        sender = _RecordingSender((EmailSendOutcome.SUCCEEDED,))

        result = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=_AccessUrlBuilder(),
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )

        delivery.refresh_from_db()
        self.assertIsNone(result)
        self.assertEqual(sender.messages, [])
        self.assertEqual(delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(delivery.attempt_count, 0)
        self.assertIsNone(delivery.lease_id)
        self.assertIsNone(delivery.lease_expires_at)

    def test_revocation_during_provider_io_keeps_the_accepted_send_result(self) -> None:
        """Revocation cannot rewrite an outcome accepted after the provider call crossed I/O."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        sender = _RevokingSender(grant=self.grant, now=self.now)

        result = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret=self.signing_secret,
            order_access_url_for_grant=_AccessUrlBuilder(),
            support_contact=self.support_contact,
            timeout_seconds=15,
            now=self.now,
        )

        delivery.refresh_from_db()
        self.assertEqual(result, delivery)
        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(EmailDeliveryAttempt.objects.filter(delivery=delivery).count(), 1)

    def test_post_provider_result_recording_locks_only_delivery_before_revocation(self) -> None:
        """Result persistence yields Order/grant locking to revocation without loss."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        record_locked = ThreadEvent()
        release_record = ThreadEvent()
        result_backend_ready = ThreadEvent()
        revoke_backend_ready = ThreadEvent()
        result_backend_pids: list[int] = []
        revoke_backend_pids: list[int] = []
        original_create = EmailDeliveryAttempt.objects.create

        def pause_after_result_lock(*args, **kwargs):
            record_locked.set()
            if not release_record.wait(timeout=5):
                raise AssertionError("The test did not release result recording.")
            return original_create(*args, **kwargs)

        def record_result_on_separate_connection() -> EmailDelivery | None:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    result_backend_pids.append(cursor.fetchone()[0])
                result_backend_ready.set()
                return send_claimed_email_delivery(
                    claim=claim,
                    email_sender=_RecordingSender((EmailSendOutcome.SUCCEEDED,)),
                    order_access_signing_secret=self.signing_secret,
                    order_access_url_for_grant=_AccessUrlBuilder(),
                    support_contact=self.support_contact,
                    timeout_seconds=15,
                    now=self.now,
                )
            finally:
                close_old_connections()

        def revoke_on_separate_connection() -> OrderAccessGrant | None:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    revoke_backend_pids.append(cursor.fetchone()[0])
                revoke_backend_ready.set()
                return revoke_order_access_grant(self.grant, revoked_at=self.now)
            finally:
                close_old_connections()

        with (
            patch(
                "commerce.delivery.EmailDeliveryAttempt.objects.create",
                side_effect=pause_after_result_lock,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            result_future = executor.submit(record_result_on_separate_connection)
            self.assertTrue(result_backend_ready.wait(timeout=2))
            self.assertTrue(record_locked.wait(timeout=2))
            self.assertEqual(
                _row_share_locks_for_backend(backend_pid=result_backend_pids[0]),
                {EmailDelivery._meta.db_table},
            )
            revoke_future = executor.submit(revoke_on_separate_connection)
            try:
                self.assertTrue(revoke_backend_ready.wait(timeout=2))
                self.assertTrue(_backend_is_waiting_for_lock(backend_pid=revoke_backend_pids[0]))
            finally:
                release_record.set()
            recorded = result_future.result(timeout=5)
            revoked = revoke_future.result(timeout=5)

        delivery.refresh_from_db()
        self.grant.refresh_from_db()
        self.assertEqual(recorded, delivery)
        self.assertIsNotNone(revoked)
        self.assertEqual(delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(EmailDeliveryAttempt.objects.filter(delivery=delivery).count(), 1)
        self.assertIsNotNone(self.grant.revoked_at)

    def test_post_provider_retry_failure_after_revocation_cancels_the_delivery(self) -> None:
        """A fresh grant read prevents a stale joined snapshot from recreating revoked work."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        record_locked = ThreadEvent()
        release_record = ThreadEvent()
        original_create = EmailDeliveryAttempt.objects.create

        def pause_after_result_lock(*args, **kwargs):
            record_locked.set()
            if not release_record.wait(timeout=5):
                raise AssertionError("The test did not release result recording.")
            return original_create(*args, **kwargs)

        def record_retryable_result() -> EmailDelivery | None:
            close_old_connections()
            try:
                return send_claimed_email_delivery(
                    claim=claim,
                    email_sender=_RecordingSender((EmailSendOutcome.RETRYABLE_FAILURE,)),
                    order_access_signing_secret=self.signing_secret,
                    order_access_url_for_grant=_AccessUrlBuilder(),
                    support_contact=self.support_contact,
                    timeout_seconds=15,
                    now=self.now,
                )
            finally:
                close_old_connections()

        def revoke_on_separate_connection() -> OrderAccessGrant | None:
            close_old_connections()
            try:
                return revoke_order_access_grant(self.grant, revoked_at=self.now)
            finally:
                close_old_connections()

        with (
            patch(
                "commerce.delivery.EmailDeliveryAttempt.objects.create",
                side_effect=pause_after_result_lock,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            result_future = executor.submit(record_retryable_result)
            self.assertTrue(record_locked.wait(timeout=2))
            revoke_future = executor.submit(revoke_on_separate_connection)
            release_record.set()
            recorded = result_future.result(timeout=5)
            self.assertIsNotNone(revoke_future.result(timeout=5))

        delivery.refresh_from_db()
        self.assertEqual(recorded, delivery)
        self.assertEqual(delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(delivery.attempt_count, 1)
        attempt = EmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(attempt.outcome, EmailDeliveryAttempt.Outcome.RETRYABLE_FAILURE)

    def test_revoke_waits_for_result_then_cancels_its_retryable_delivery(self) -> None:
        """A result that read an active grant cannot leave hidden revoked retry work behind."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        grant_read = ThreadEvent()
        release_result = ThreadEvent()
        revoke_backend_ready = ThreadEvent()
        revoke_backend_pids: list[int] = []

        from commerce import delivery as delivery_module

        original_grant_is_revoked = delivery_module._delivery_grant_is_revoked

        def pause_after_active_grant_read(*, grant_id):
            is_revoked = original_grant_is_revoked(grant_id=grant_id)
            grant_read.set()
            if not release_result.wait(timeout=5):
                raise AssertionError("The test did not release result recording.")
            return is_revoked

        def record_retryable_result() -> EmailDelivery | None:
            close_old_connections()
            try:
                return send_claimed_email_delivery(
                    claim=claim,
                    email_sender=_RecordingSender((EmailSendOutcome.RETRYABLE_FAILURE,)),
                    order_access_signing_secret=self.signing_secret,
                    order_access_url_for_grant=_AccessUrlBuilder(),
                    support_contact=self.support_contact,
                    timeout_seconds=15,
                    now=self.now,
                )
            finally:
                close_old_connections()

        def revoke_on_separate_connection() -> OrderAccessGrant | None:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    revoke_backend_pids.append(cursor.fetchone()[0])
                revoke_backend_ready.set()
                return revoke_order_access_grant(self.grant, revoked_at=self.now)
            finally:
                close_old_connections()

        with (
            patch(
                "commerce.delivery._delivery_grant_is_revoked",
                side_effect=pause_after_active_grant_read,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            result_future = executor.submit(record_retryable_result)
            self.assertTrue(grant_read.wait(timeout=2))
            revoke_future = executor.submit(revoke_on_separate_connection)
            try:
                self.assertTrue(revoke_backend_ready.wait(timeout=2))
                self.assertTrue(_backend_is_waiting_for_lock(backend_pid=revoke_backend_pids[0]))
            finally:
                release_result.set()
            recorded = result_future.result(timeout=5)
            revoked = revoke_future.result(timeout=5)

        delivery.refresh_from_db()
        self.grant.refresh_from_db()
        self.assertIsNotNone(recorded)
        self.assertIsNotNone(revoked)
        self.assertEqual(delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(claim_due_email_deliveries(now=self.now), ())
        health = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(0),
            worker_is_alive=lambda: True,
        )
        self.assertTrue(health.healthy)
        self.assertIsNone(health.oldest_ready_work_type)
        attempt = EmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(attempt.outcome, EmailDeliveryAttempt.Outcome.RETRYABLE_FAILURE)
        self.assertIsNotNone(self.grant.revoked_at)

    def test_correction_waits_for_old_email_result_then_cancels_retryable_delivery(self) -> None:
        """A stale joined recipient read cannot recreate old-recipient retries after correction."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        old_email_read = ThreadEvent()
        release_result = ThreadEvent()
        correction_backend_ready = ThreadEvent()
        correction_backend_pids: list[int] = []

        from commerce import delivery as delivery_module

        original_recipient_is_current = delivery_module._delivery_recipient_is_current

        def pause_after_old_order_email(*, delivery):
            is_current = original_recipient_is_current(delivery=delivery)
            old_email_read.set()
            if not release_result.wait(timeout=5):
                raise AssertionError("The test did not release result recording.")
            return is_current

        def record_retryable_result() -> EmailDelivery | None:
            close_old_connections()
            try:
                return send_claimed_email_delivery(
                    claim=claim,
                    email_sender=_RecordingSender((EmailSendOutcome.RETRYABLE_FAILURE,)),
                    order_access_signing_secret=self.signing_secret,
                    order_access_url_for_grant=_AccessUrlBuilder(),
                    support_contact=self.support_contact,
                    timeout_seconds=15,
                    now=self.now,
                )
            finally:
                close_old_connections()

        def correct_on_separate_connection() -> Order:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    correction_backend_pids.append(cursor.fetchone()[0])
                correction_backend_ready.set()
                return correct_delivery_email(
                    order_id=self.order.pk,
                    delivery_email="corrected@example.test",
                    now=self.now,
                )
            finally:
                close_old_connections()

        with (
            patch(
                "commerce.delivery._delivery_recipient_is_current",
                side_effect=pause_after_old_order_email,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            result_future = executor.submit(record_retryable_result)
            self.assertTrue(old_email_read.wait(timeout=2))
            correction_future = executor.submit(correct_on_separate_connection)
            try:
                self.assertTrue(correction_backend_ready.wait(timeout=2))
                self.assertTrue(
                    _backend_is_waiting_for_lock(backend_pid=correction_backend_pids[0])
                )
            finally:
                release_result.set()
            recorded = result_future.result(timeout=5)
            corrected_order = correction_future.result(timeout=5)

        delivery.refresh_from_db()
        self.order.refresh_from_db()
        self.assertIsNotNone(recorded)
        self.assertEqual(corrected_order.pk, self.order.pk)
        self.assertEqual(self.order.delivery_email, "corrected@example.test")
        self.assertEqual(delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(claim_due_email_deliveries(now=self.now), ())
        health = commerce_worker_health(
            now=self.now,
            max_ready_age=timedelta(0),
            worker_is_alive=lambda: True,
        )
        self.assertTrue(health.healthy)
        self.assertIsNone(health.oldest_ready_work_type)
        attempt = EmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(attempt.recipient_email, "buyer@example.test")
        self.assertEqual(attempt.outcome, EmailDeliveryAttempt.Outcome.RETRYABLE_FAILURE)

    def test_correction_waits_for_result_then_preserves_accepted_success(self) -> None:
        """Correction cannot rewrite an in-flight accepted success or immutable attempt."""
        delivery = self.create_delivery()
        claim = claim_due_email_deliveries(now=self.now, limit=1)[0]
        record_locked = ThreadEvent()
        release_result = ThreadEvent()
        correction_backend_ready = ThreadEvent()
        correction_backend_pids: list[int] = []
        original_create = EmailDeliveryAttempt.objects.create

        def pause_after_result_lock(*args, **kwargs):
            record_locked.set()
            if not release_result.wait(timeout=5):
                raise AssertionError("The test did not release result recording.")
            return original_create(*args, **kwargs)

        def record_success() -> EmailDelivery | None:
            close_old_connections()
            try:
                return send_claimed_email_delivery(
                    claim=claim,
                    email_sender=_RecordingSender((EmailSendOutcome.SUCCEEDED,)),
                    order_access_signing_secret=self.signing_secret,
                    order_access_url_for_grant=_AccessUrlBuilder(),
                    support_contact=self.support_contact,
                    timeout_seconds=15,
                    now=self.now,
                )
            finally:
                close_old_connections()

        def correct_on_separate_connection() -> Order:
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    correction_backend_pids.append(cursor.fetchone()[0])
                correction_backend_ready.set()
                return correct_delivery_email(
                    order_id=self.order.pk,
                    delivery_email="corrected@example.test",
                    now=self.now,
                )
            finally:
                close_old_connections()

        with (
            patch(
                "commerce.delivery.EmailDeliveryAttempt.objects.create",
                side_effect=pause_after_result_lock,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            result_future = executor.submit(record_success)
            self.assertTrue(record_locked.wait(timeout=2))
            correction_future = executor.submit(correct_on_separate_connection)
            try:
                self.assertTrue(correction_backend_ready.wait(timeout=2))
                self.assertTrue(
                    _backend_is_waiting_for_lock(backend_pid=correction_backend_pids[0])
                )
            finally:
                release_result.set()
            recorded = result_future.result(timeout=5)
            corrected_order = correction_future.result(timeout=5)

        delivery.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(recorded, delivery)
        self.assertEqual(corrected_order.pk, self.order.pk)
        self.assertEqual(self.order.delivery_email, "corrected@example.test")
        self.assertEqual(delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(delivery.attempt_count, 1)
        attempt = EmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(attempt.recipient_email, "buyer@example.test")
        self.assertEqual(attempt.outcome, EmailDeliveryAttempt.Outcome.SUCCEEDED)

    def test_concurrent_retry_and_delivery_correction_leave_no_old_recipient_work(self) -> None:
        """The Order lock makes either correction/retry ordering safe for the old recipient."""
        failed_delivery = self.create_delivery()
        failed_delivery.state = EmailDelivery.State.FAILED
        failed_delivery.save(update_fields=["state"])
        start = Barrier(2)

        def retry_from_separate_connection() -> None:
            close_old_connections()
            start.wait(timeout=2)
            try:
                retry_failed_email_delivery(delivery_id=failed_delivery.pk)
            finally:
                close_old_connections()

        def correct_from_separate_connection() -> None:
            close_old_connections()
            start.wait(timeout=2)
            try:
                correct_delivery_email(
                    order_id=self.order.pk,
                    delivery_email="corrected@example.test",
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(retry_from_separate_connection),
                executor.submit(correct_from_separate_connection),
            )
            for future in futures:
                future.result(timeout=5)

        failed_delivery.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.order.delivery_email, "corrected@example.test")
        self.assertEqual(failed_delivery.state, EmailDelivery.State.CANCELED)
        self.assertEqual(claim_due_email_deliveries(now=self.now), ())
