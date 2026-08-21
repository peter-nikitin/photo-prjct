from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event as ThreadEvent
from unittest.mock import patch

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from picflow.models import Event, Photo

from commerce import payments as payment_services
from commerce import services as cart_services
from commerce.identity import browser_token_sha256
from commerce.models import (
    Cart,
    CartItem,
    CommerceAttention,
    EmailDelivery,
    Order,
    OrderAccessGrant,
    OrderItem,
    PaymentAttempt,
    PaymentEvidence,
)
from commerce.payment_gateway import (
    CreatedPayment,
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentObservation,
    PaymentRequest,
)
from commerce.payments import (
    PaymentReconciliationUnavailable,
    PaymentTransitionRejected,
    apply_authenticated_notification,
    apply_payment_observation,
    cancel_order,
    mark_order_paid_manually,
    reconcile_payment_attempt,
)
from commerce.services import clear_cart, set_photo_selected


class _ObservationGateway:
    adapter_key = "deterministic-test"

    def __init__(self, observation: PaymentObservation) -> None:
        self.observation = observation
        self.fetch_in_atomic_blocks: list[bool] = []
        self.notifications: list[IncomingPaymentNotification] = []

    def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        raise AssertionError("The observation-only test gateway must not create payments.")

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        self.fetch_in_atomic_blocks.append(connection.in_atomic_block)
        if provider_payment_id != self.observation.provider_payment_id:
            raise AssertionError("The reconciliation fetched a different provider payment.")
        return self.observation

    def authenticate_notification(
        self,
        notification: IncomingPaymentNotification,
    ) -> PaymentObservation:
        self.notifications.append(notification)
        return self.observation


class _FlakyObservationGateway(_ObservationGateway):
    def __init__(self, observation: PaymentObservation) -> None:
        super().__init__(observation)
        self.unavailable = True

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        if self.unavailable:
            raise PaymentGatewayError(PaymentGatewayErrorCategory.UNAVAILABLE)
        return super().fetch_payment(provider_payment_id)


class _StaffShapedActor:
    is_active = True
    is_authenticated = True
    is_staff = True

    def __init__(self, pk: int) -> None:
        self.pk = pk


class PaymentTransitionTests(TransactionTestCase):
    """The breaks caught here would grant originals without exact authoritative payment evidence."""

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        self.cart_token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
        self.cart_digest = browser_token_sha256(self.cart_token)
        self.event = Event.objects.create(
            name="Payment event",
            slug="payment-event",
            start_date=self.now.date(),
            end_date=self.now.date(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.other_event = Event.objects.create(
            name="Other payment event",
            slug="other-payment-event",
            start_date=self.now.date(),
            end_date=self.now.date(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="payment-photo",
            event=self.event,
            src="photos/payment.jpg",
        )
        self.added_photo = Photo.objects.create(
            id="payment-added-photo",
            event=self.event,
            src="photos/payment-added.jpg",
        )
        self.other_photo = Photo.objects.create(
            id="payment-other-photo",
            event=self.other_event,
            src="photos/payment-other.jpg",
        )
        self.cart = Cart.objects.create(
            event=self.event,
            browser_token_sha256=self.cart_digest,
            expires_at=self.now + timedelta(days=1),
        )
        CartItem.objects.create(cart=self.cart, photo=self.photo)
        self.other_cart = Cart.objects.create(
            event=self.other_event,
            browser_token_sha256=self.cart_digest,
            expires_at=self.now + timedelta(days=1),
        )
        CartItem.objects.create(cart=self.other_cart, photo=self.other_photo)
        with transaction.atomic():
            self.order = Order.objects.create(
                public_number="FM-PAYMNT42",
                event=self.event,
                originating_cart_token_sha256=self.cart_digest,
                purchase_browser_token_sha256="b" * 64,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
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
        self.attempt = PaymentAttempt.objects.create(
            order=self.order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="deterministic-test",
            idempotency_key="payment-attempt-1",
            provider_payment_id="provider-payment-1",
            expires_at=self.now + timedelta(hours=1),
        )
        self.operator = get_user_model().objects.create_user(
            username="payment-operator",
            is_staff=True,
        )

    def observation(
        self,
        *,
        status: NormalizedPaymentStatus = NormalizedPaymentStatus.SUCCEEDED,
        amount_kopecks: int = 30000,
        currency: str = "RUB",
    ) -> PaymentObservation:
        return PaymentObservation(
            provider_payment_id=self.attempt.provider_payment_id,
            status=status,
            amount_kopecks=amount_kopecks,
            currency=currency,
            idempotency_key=self.attempt.idempotency_key,
            provider_event_id="provider-event-safe-1",
        )

    def apply(
        self,
        observation: PaymentObservation,
        *,
        source: str = "notification",
    ) -> Order:
        return apply_payment_observation(
            attempt_id=self.attempt.pk,
            adapter_key="deterministic-test",
            source=source,
            observation=observation,
            now=self.now,
        )

    def test_exact_authenticated_success_pays_once_and_cleans_only_originating_purchased_positions(
        self,
    ) -> None:
        """A broad cart clear or second email job would corrupt callback-retry fulfillment."""
        CartItem.objects.create(cart=self.cart, photo=self.added_photo)

        self.apply(self.observation())
        self.apply(self.observation())

        self.order.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.paid_at, self.now)
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(self.attempt.terminal_at, self.now)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.assertFalse(CartItem.objects.filter(cart=self.cart, photo=self.photo).exists())
        self.assertTrue(CartItem.objects.filter(cart=self.cart, photo=self.added_photo).exists())
        self.assertTrue(
            CartItem.objects.filter(cart=self.other_cart, photo=self.other_photo).exists()
        )
        self.assertEqual(PaymentEvidence.objects.filter(payment_attempt=self.attempt).count(), 2)

    def test_authenticated_notification_and_status_fetch_use_the_same_normalized_transition(
        self,
    ) -> None:
        """Divergent callback and reconciliation paths could authorize different payment facts."""
        gateway = _ObservationGateway(self.observation())

        apply_authenticated_notification(
            gateway=gateway,
            notification=self.incoming_notification(),
            now=self.now,
        )
        reconcile_payment_attempt(
            attempt_id=self.attempt.pk,
            gateway=gateway,
            now=self.now,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(len(gateway.notifications), 1)
        self.assertEqual(gateway.fetch_in_atomic_blocks, [False])
        self.assertEqual(
            set(PaymentEvidence.objects.values_list("source", flat=True)),
            {PaymentEvidence.Source.NOTIFICATION, PaymentEvidence.Source.STATUS_FETCH},
        )

    def test_expiry_fetches_before_marking_a_still_pending_attempt_terminal(self) -> None:
        """Expiring without a current provider fetch could discard a late successful payment."""
        pending = self.observation(status=NormalizedPaymentStatus.PENDING)
        gateway = _ObservationGateway(pending)

        reconcile_payment_attempt(
            attempt_id=self.attempt.pk,
            gateway=gateway,
            now=self.now + timedelta(hours=2),
        )

        self.attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(gateway.fetch_in_atomic_blocks, [False])
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.EXPIRED)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(EmailDelivery.objects.count(), 0)

    def test_a_later_successful_fetch_resolves_its_transient_reconciliation_attention(self) -> None:
        """A repaired fetch must stop escalating the resolved reconciliation failure."""
        gateway = _FlakyObservationGateway(self.observation())

        with self.assertRaises(PaymentReconciliationUnavailable):
            reconcile_payment_attempt(
                attempt_id=self.attempt.pk,
                gateway=gateway,
                now=self.now,
            )
        attention = CommerceAttention.objects.get(
            kind=CommerceAttention.Kind.PAYMENT_RECONCILIATION_OVERDUE,
            subject=f"payment-attempt:{self.attempt.pk}",
        )

        gateway.unavailable = False
        reconcile_payment_attempt(
            attempt_id=self.attempt.pk,
            gateway=gateway,
            now=self.now + timedelta(minutes=1),
        )

        attention.refresh_from_db()
        self.assertIsNotNone(attention.resolved_at)
        self.assertEqual(
            attention.resolution_source,
            CommerceAttention.ResolutionSource.AUTOMATIC,
        )

    def test_amount_mismatch_conflicts_without_entitlement(self) -> None:
        """Accepting a rounded provider amount would grant the wrong immutable Order."""
        self.apply(self.observation(amount_kopecks=29999))

        self.attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.CONFLICT)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(
            CommerceAttention.objects.filter(
                kind=CommerceAttention.Kind.PAYMENT_MISMATCH,
                subject=f"payment-attempt:{self.attempt.pk}",
                resolved_at__isnull=True,
            ).count(),
            1,
        )

    def test_authenticated_non_rub_observation_opens_conflict_and_persists_exact_evidence(
        self,
    ) -> None:
        """Authenticated USD must explain why fulfillment was denied."""
        observation = self.observation(currency="USD")
        gateway = _ObservationGateway(observation)

        apply_authenticated_notification(
            gateway=gateway,
            notification=self.incoming_notification(),
            now=self.now,
        )

        self.attempt.refresh_from_db()
        self.order.refresh_from_db()
        evidence = PaymentEvidence.objects.get(payment_attempt=self.attempt)
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.CONFLICT)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(evidence.currency, "USD")
        self.assertEqual(evidence.amount_kopecks, 30000)
        self.assertEqual(evidence.normalized_status, PaymentAttempt.Status.SUCCEEDED)

    def test_provider_identity_and_idempotency_must_match_the_persisted_attempt(self) -> None:
        """Applying another payment's callback would substitute external evidence for this order."""
        for attribute, value in (
            ("provider_payment_id", "provider-payment-other"),
            ("idempotency_key", "another-attempt"),
        ):
            with self.subTest(attribute=attribute):
                observation = self.observation()
                object.__setattr__(observation, attribute, value)
                with self.assertRaises(PaymentTransitionRejected):
                    self.apply(observation)

        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(PaymentEvidence.objects.count(), 0)
        self.assertEqual(EmailDelivery.objects.count(), 0)

    def test_late_verified_success_fulfills_a_superseded_snapshot(self) -> None:
        """Dropping late real money after cart mutation would leave an obligation unfulfilled."""
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.SUPERSEDED)
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(
            status=PaymentAttempt.Status.EXPIRED,
            terminal_at=self.now - timedelta(minutes=1),
        )
        CartItem.objects.create(cart=self.cart, photo=self.added_photo)

        self.apply(self.observation())

        self.order.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.assertFalse(CartItem.objects.filter(cart=self.cart, photo=self.photo).exists())
        self.assertTrue(CartItem.objects.filter(cart=self.cart, photo=self.added_photo).exists())

    def test_late_success_for_a_canceled_order_stays_closed_and_opens_attention(self) -> None:
        """A callback must not reopen an Order an operator deliberately canceled."""
        cancel_order(order_id=self.order.pk, actor=self.operator, now=self.now)

        self.apply(self.observation())

        self.order.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.CONFLICT)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(CommerceAttention.objects.filter(resolved_at__isnull=True).count(), 1)

    def test_manual_paid_bypasses_provider_evidence_but_uses_atomic_fulfillment(self) -> None:
        """Manual recovery must not require provider evidence or skip customer delivery."""
        CartItem.objects.create(cart=self.cart, photo=self.added_photo)

        mark_order_paid_manually(order_id=self.order.pk, actor=self.operator, now=self.now)

        self.order.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.paid_at, self.now)
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(PaymentEvidence.objects.count(), 0)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        audit = LogEntry.objects.get(object_id=str(self.order.pk))
        self.assertEqual(audit.user_id, self.operator.pk)
        self.assertEqual(audit.action_flag, CHANGE)
        self.assertIsNotNone(audit.action_time)
        self.assertFalse(CartItem.objects.filter(cart=self.cart, photo=self.photo).exists())
        self.assertTrue(CartItem.objects.filter(cart=self.cart, photo=self.added_photo).exists())

    def test_manual_paid_rejects_a_nonstaff_actor(self) -> None:
        """A nonstaff caller must not turn an Order into a paid entitlement."""
        untrusted = get_user_model().objects.create_user(username="untrusted-payment-user")

        with self.assertRaises(PaymentTransitionRejected):
            mark_order_paid_manually(order_id=self.order.pk, actor=untrusted, now=self.now)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_manual_paid_rejects_an_inactive_staff_user(self) -> None:
        """An inactive staff record is not an authenticated operator for commercial recovery."""
        inactive = get_user_model().objects.create_user(
            username="inactive-payment-operator",
            is_staff=True,
            is_active=False,
        )

        with self.assertRaises(PaymentTransitionRejected):
            mark_order_paid_manually(order_id=self.order.pk, actor=inactive, now=self.now)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_manual_paid_rejects_a_staff_shaped_nonuser(self) -> None:
        """A lookalike object must not borrow a real user's primary key as Admin authority."""
        with self.assertRaises(PaymentTransitionRejected):
            mark_order_paid_manually(
                order_id=self.order.pk,
                actor=_StaffShapedActor(self.operator.pk),
                now=self.now,
            )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_cancel_requires_a_trusted_staff_actor_and_writes_admin_history(self) -> None:
        """Cancellation must retain accountable staff authority."""
        cancel_order(order_id=self.order.pk, actor=self.operator, now=self.now)

        self.order.refresh_from_db()
        audit = LogEntry.objects.get(object_id=str(self.order.pk))
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self.assertEqual(audit.user_id, self.operator.pk)
        self.assertEqual(audit.action_flag, CHANGE)
        self.assertIsNotNone(audit.action_time)

    def test_cancel_rejects_an_inactive_staff_user(self) -> None:
        """Inactive staff must not close an Order through the exported domain command."""
        inactive = get_user_model().objects.create_user(
            username="inactive-cancel-operator",
            is_staff=True,
            is_active=False,
        )

        with self.assertRaises(PaymentTransitionRejected):
            cancel_order(order_id=self.order.pk, actor=inactive, now=self.now)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_cancel_rejects_nonstaff_and_staff_shaped_nonuser(self) -> None:
        """Cancellation requires the same genuine active staff authority as manual payment."""
        nonstaff = get_user_model().objects.create_user(username="untrusted-cancel-user")
        for actor in (nonstaff, _StaffShapedActor(self.operator.pk)):
            with (
                self.subTest(actor=type(actor).__name__),
                self.assertRaises(PaymentTransitionRejected),
            ):
                cancel_order(order_id=self.order.pk, actor=actor, now=self.now)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(LogEntry.objects.count(), 0)

    def test_compatible_late_success_resolves_a_manual_payment_conflict(self) -> None:
        """A later matching bank success must close the manual conflict it repairs."""
        mark_order_paid_manually(order_id=self.order.pk, actor=self.operator, now=self.now)
        self.apply(self.observation(status=NormalizedPaymentStatus.CANCELED))
        attention = CommerceAttention.objects.get(
            kind=CommerceAttention.Kind.MANUAL_PAYMENT_CONFLICT,
            subject=f"payment-attempt:{self.attempt.pk}",
        )

        self.apply(self.observation())

        attention.refresh_from_db()
        self.assertIsNotNone(attention.resolved_at)
        self.assertEqual(
            attention.resolution_source,
            CommerceAttention.ResolutionSource.AUTOMATIC,
        )

    def test_manual_and_automatic_paid_race_create_one_fulfillment(self) -> None:
        """Independent manual and callback paths must share one fulfillment lock."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._run_manual_paid),
                executor.submit(self._run_automatic_success),
            ]
            outcomes = [future.result(timeout=5) for future in futures]

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.assertFalse(CartItem.objects.filter(cart=self.cart, photo=self.photo).exists())
        self.assertIn("paid", outcomes)

    def _run_manual_paid(self) -> str:
        close_old_connections()
        try:
            mark_order_paid_manually(order_id=self.order.pk, actor=self.operator, now=self.now)
            return "paid"
        except PaymentTransitionRejected:
            return "rejected"
        finally:
            close_old_connections()

    def _run_automatic_success(self) -> str:
        close_old_connections()
        try:
            apply_payment_observation(
                attempt_id=self.attempt.pk,
                adapter_key="deterministic-test",
                source="notification",
                observation=self.observation(),
                now=self.now,
            )
            return "paid"
        finally:
            close_old_connections()

    def test_equivalent_concurrent_notifications_are_idempotent(self) -> None:
        """Concurrent duplicate callbacks must create one paid transition and email job."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._run_automatic_success) for _ in range(2)]
            [future.result(timeout=5) for future in futures]

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.assertFalse(CartItem.objects.filter(cart=self.cart, photo=self.photo).exists())

    def test_equivalent_concurrent_notification_and_fetch_share_one_transition(self) -> None:
        """Callback and worker fetch races must not duplicate one paid fulfillment transition."""
        gateway = _ObservationGateway(self.observation())
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._run_authenticated_notification, gateway),
                executor.submit(self._run_status_fetch, gateway),
            ]
            [future.result(timeout=5) for future in futures]

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.assertEqual(
            set(PaymentEvidence.objects.values_list("source", flat=True)),
            {PaymentEvidence.Source.NOTIFICATION, PaymentEvidence.Source.STATUS_FETCH},
        )

    def test_terminal_unsuccessful_observation_unlocks_real_cart_set_and_clear_mutations(
        self,
    ) -> None:
        """A canceled attempt must release the exact cart for normal later mutation and clearing."""
        self.apply(self.observation(status=NormalizedPaymentStatus.CANCELED))

        with self._cart_purchasable(self.photo, self.added_photo):
            selected = set_photo_selected(
                event=self.event,
                photo_id=self.added_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
            cleared = clear_cart(event=self.event, browser_token=self.cart_token)

        self.order.refresh_from_db()
        self.assertTrue(selected.changed)
        self.assertTrue(cleared.changed)
        self.assertEqual(self.order.status, Order.Status.SUPERSEDED)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_automatic_paid_and_real_cart_mutation_complete_without_lock_cycle(self) -> None:
        """Callback fulfillment and cart mutation must use the same Event-to-Attempt lock order."""
        self._assert_paid_cart_race_completes(
            self._run_automatic_success,
            pause_target="commerce.payments._set_attempt_terminal",
            pause_after_original=True,
        )

    def test_manual_paid_and_real_cart_mutation_complete_without_lock_cycle(self) -> None:
        """Trusted manual fulfillment must use the same cart-locking helper as a callback."""
        self._assert_paid_cart_race_completes(
            self._run_manual_paid,
            pause_target="commerce.payments._fulfill_paid_order",
            pause_after_original=False,
        )

    def _cart_purchasable(self, *photos: Photo):
        return patch(
            "commerce.services.purchasable_paid_photo_queryset",
            side_effect=lambda *, event, watermarked_previews_enabled: Photo.objects.filter(
                event=event,
                pk__in=[photo.pk for photo in photos],
            ),
        )

    def _run_authenticated_notification(self, gateway: _ObservationGateway) -> None:
        close_old_connections()
        try:
            apply_authenticated_notification(
                gateway=gateway,
                notification=self.incoming_notification(),
                now=self.now,
            )
        finally:
            close_old_connections()

    def _run_status_fetch(self, gateway: _ObservationGateway) -> None:
        close_old_connections()
        try:
            reconcile_payment_attempt(attempt_id=self.attempt.pk, gateway=gateway, now=self.now)
        finally:
            close_old_connections()

    def _assert_paid_cart_race_completes(
        self,
        paid_command,
        *,
        pause_target: str,
        pause_after_original: bool,
    ) -> None:
        transition_reached = ThreadEvent()
        cart_locked = ThreadEvent()
        original_transition = (
            payment_services._set_attempt_terminal
            if pause_target.endswith("_set_attempt_terminal")
            else payment_services._fulfill_paid_order
        )
        original_locked_cart = cart_services._locked_current_cart

        def pause_between_order_and_cleanup(*args, **kwargs):
            if not pause_after_original:
                transition_reached.set()
                cart_locked.wait(timeout=0.3)
            result = original_transition(*args, **kwargs)
            if pause_after_original:
                transition_reached.set()
                cart_locked.wait(timeout=0.3)
            return result

        def record_cart_lock(*args, **kwargs):
            cart = original_locked_cart(*args, **kwargs)
            if cart is not None:
                cart_locked.set()
            return cart

        with (
            self._cart_purchasable(self.photo, self.added_photo),
            patch(pause_target, side_effect=pause_between_order_and_cleanup),
            patch("commerce.services._locked_current_cart", side_effect=record_cart_lock),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            paid_future = executor.submit(paid_command)
            self.assertTrue(transition_reached.wait(timeout=2))
            cart_future = executor.submit(self._run_real_cart_add)
            paid_future.result(timeout=5)
            cart_future.result(timeout=5)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.filter(order=self.order).count(), 1)
        self.assertTrue(
            CartItem.objects.filter(
                cart__event=self.event,
                photo=self.added_photo,
            ).exists()
        )

    def _run_real_cart_add(self) -> None:
        close_old_connections()
        try:
            set_photo_selected(
                event=Event.objects.get(pk=self.event.pk),
                photo_id=self.added_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        finally:
            close_old_connections()

    def incoming_notification(self) -> IncomingPaymentNotification:
        return IncomingPaymentNotification(headers={}, body=b"authenticated-by-test-gateway")
