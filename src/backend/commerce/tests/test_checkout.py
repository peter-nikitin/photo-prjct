from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event as ThreadEvent
from threading import Lock
from time import monotonic, sleep
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone
from django.views.debug import technical_500_response
from picflow.models import Event, Photo

from commerce.capabilities import purchase_browser_authorizes_order
from commerce.checkout import (
    CheckoutEmailMismatch,
    CheckoutEmptyCart,
    CheckoutPaymentUnavailable,
    create_checkout,
)
from commerce.identity import browser_token_sha256
from commerce.models import (
    Cart,
    CartItem,
    EmailDelivery,
    Order,
    OrderAccessGrant,
    OrderItem,
    PaymentAttempt,
)
from commerce.payment_gateway import (
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentRequest,
)
from commerce.services import clear_cart, read_cart, set_photo_selected
from commerce.test_payment_gateway import DeterministicPaymentGateway, TestPaymentOutcome

DIAGNOSTIC_CHECKOUT_EMAIL = "Buyer.Secret@example.test"
DIAGNOSTIC_NORMALIZED_EMAIL = "buyer.secret@example.test"


class RecordingGateway(DeterministicPaymentGateway):
    def __init__(self, *, adapter_key: str = "deterministic-test", **kwargs) -> None:
        super().__init__(adapter_key=adapter_key, **kwargs)
        self.in_atomic_blocks: list[bool] = []
        self.persisted_attempt_ids: list[int] = []
        self.requests: list[PaymentRequest] = []
        self._recording_lock = Lock()

    def create_payment(self, request):
        attempt = PaymentAttempt.objects.get(idempotency_key=request.idempotency_key)
        with self._recording_lock:
            self.in_atomic_blocks.append(connection.in_atomic_block)
            self.persisted_attempt_ids.append(attempt.pk)
            self.requests.append(request)
        return super().create_payment(request)


class TimeoutOnceGateway(RecordingGateway):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timed_out = False

    def create_payment(self, request):
        created = super().create_payment(request)
        if not self._timed_out:
            self._timed_out = True
            raise PaymentGatewayError(PaymentGatewayErrorCategory.UNAVAILABLE)
        return created


class RawProviderResultGateway(RecordingGateway):
    def create_payment(self, request):
        super().create_payment(request)
        return {"provider_sdk_payment": "raw"}


class ReplacedAttemptGateway(RecordingGateway):
    def create_payment(self, request):
        created = super().create_payment(request)
        attempt = PaymentAttempt.objects.get(idempotency_key=request.idempotency_key)
        now = timezone.now()
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            status=PaymentAttempt.Status.FAILED,
            terminal_at=now,
        )
        PaymentAttempt.objects.create(
            order=attempt.order,
            amount_kopecks=attempt.amount_kopecks,
            currency=attempt.currency,
            adapter_key=attempt.adapter_key,
            idempotency_key="replacement-current-attempt",
        )
        return created


class BlockingCreateGateway(RecordingGateway):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.create_started = ThreadEvent()
        self.release_create = ThreadEvent()

    def create_payment(self, request):
        self.create_started.set()
        if not self.release_create.wait(timeout=2):
            raise AssertionError("Timed out waiting to release deterministic create.")
        return super().create_payment(request)


class ReconciliationRaceGateway(RecordingGateway):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.response_created = ThreadEvent()
        self.release_response = ThreadEvent()
        self.reconciliation_backend_pid: int | None = None

    def create_payment(self, request):
        created = super().create_payment(request)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            [backend_pid] = cursor.fetchone()
        self.reconciliation_backend_pid = backend_pid
        self.response_created.set()
        if not self.release_response.wait(timeout=2):
            raise AssertionError("Timed out waiting to release reconciliation response.")
        return created


class CheckoutServiceTests(TransactionTestCase):
    """The breaks caught here would charge the wrong immutable cart or duplicate payment work."""

    cart_token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    existing_purchase_token = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"

    def setUp(self) -> None:
        self.now = timezone.now()
        self.event = Event.objects.create(
            name="Checkout event",
            slug="checkout-event",
            start_date=date(2026, 8, 21),
            end_date=date(2026, 8, 21),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.other_event = Event.objects.create(
            name="Other checkout event",
            slug="other-checkout-event",
            start_date=date(2026, 8, 21),
            end_date=date(2026, 8, 21),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=45000,
        )
        self.first_photo = Photo.objects.create(
            id="checkout-photo-one",
            event=self.event,
            src="photos/checkout-one.jpg",
        )
        self.second_photo = Photo.objects.create(
            id="checkout-photo-two",
            event=self.event,
            src="photos/checkout-two.jpg",
        )
        self.other_photo = Photo.objects.create(
            id="checkout-photo-other",
            event=self.other_event,
            src="photos/checkout-other.jpg",
        )
        self.cart = Cart.objects.create(
            event=self.event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=self.now + timedelta(days=1),
        )
        CartItem.objects.create(cart=self.cart, photo=self.first_photo)

    def gateway(self, **kwargs) -> RecordingGateway:
        return RecordingGateway(
            outcome=kwargs.pop("outcome", TestPaymentOutcome.PENDING),
            notification_secret=b"checkout-test-secret",
            now=self.now,
            **kwargs,
        )

    def purchasable(self, *photos: Photo):
        ids = tuple(photo.pk for photo in photos)
        return patch(
            "commerce.checkout.purchasable_paid_photo_queryset",
            side_effect=lambda *, event, watermarked_previews_enabled: Photo.objects.filter(
                event=event,
                pk__in=ids,
            ),
        )

    def cart_purchasable(self, *photos: Photo):
        ids = tuple(photo.pk for photo in photos)
        return patch(
            "commerce.services.purchasable_paid_photo_queryset",
            side_effect=lambda *, event, watermarked_previews_enabled: Photo.objects.filter(
                event=event,
                pk__in=ids,
            ),
        )

    def checkout(self, *, gateway=None, purchase_token=None, **kwargs):
        return create_checkout(
            event=kwargs.pop("event", self.event),
            cart_browser_token=self.cart_token,
            purchase_browser_token=purchase_token,
            checkout_email=kwargs.pop("checkout_email", " buyer@EXAMPLE.test "),
            checkout_email_confirmation=kwargs.pop(
                "checkout_email_confirmation", "buyer@example.TEST"
            ),
            watermarked_previews_enabled=True,
            purchase_enabled=True,
            adapter_key=kwargs.pop("adapter_key", "deterministic-test"),
            gateway=gateway or self.gateway(),
            return_url_for_order=lambda public_number: (
                f"https://findme.test/orders/{public_number}/return/"
            ),
            now=self.now,
            **kwargs,
        )

    def make_complete_order(self, *, public_number: str, digest: str, status: str) -> Order:
        with transaction.atomic():
            order = Order.objects.create(
                public_number=public_number,
                event=self.event,
                originating_cart_token_sha256=digest,
                checkout_email="fixture@example.test",
                total_kopecks=30000,
                status=status,
                paid_at=self.now if status == Order.Status.PAID else None,
            )
            OrderItem.objects.create(
                order=order,
                photo=self.first_photo,
                photo_public_id=self.first_photo.pk,
                unit_price_kopecks=30000,
                line_total_kopecks=30000,
            )
        return order

    def test_originating_cart_digest_is_validated_immutable_indexed_and_pending_unique(
        self,
    ) -> None:
        """A malformed or rewritten origin could merge carts or clean another browser's items."""
        digest = browser_token_sha256(self.cart_token)
        paid = self.make_complete_order(
            public_number="FM-PAJD2345",
            digest=digest,
            status=str(Order.Status.PAID),
        )
        pending = self.make_complete_order(
            public_number="FM-PEND2345",
            digest=digest,
            status=str(Order.Status.PENDING),
        )

        self.assertEqual(paid.originating_cart_token_sha256, digest)
        self.assertEqual(pending.originating_cart_token_sha256, digest)
        self.assertIn(
            "commerce_order_origin_cart_idx",
            {index.name for index in Order._meta.indexes},
        )
        pending.originating_cart_token_sha256 = "A" * 64
        with self.assertRaises(ValidationError):
            pending.full_clean()
        with self.assertRaisesRegex(ValidationError, "originating cart"):
            pending.save(update_fields=["originating_cart_token_sha256"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            Order.objects.filter(pk=pending.pk).update(originating_cart_token_sha256="b" * 64)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                public_number="FM-DUPL2345",
                event=self.event,
                originating_cart_token_sha256=digest,
                checkout_email="other@example.test",
                total_kopecks=30000,
            )

    def test_checkout_prunes_ineligible_positions_and_rejects_an_empty_cart(self) -> None:
        """Charging a stale or ineligible position would bypass the current catalog boundary."""
        with self.purchasable(), self.assertRaises(CheckoutEmptyCart):
            self.checkout()

        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_checkout_requires_two_matching_normalized_emails(self) -> None:
        """A mismatch could deliver the permanent purchase capability to the wrong address."""
        with self.purchasable(self.first_photo), self.assertRaises(CheckoutEmailMismatch):
            self.checkout(checkout_email_confirmation="different@example.test")
        self.assertEqual(Order.objects.count(), 0)

        with self.purchasable(self.first_photo):
            result = self.checkout()
        self.assertEqual(result.order.checkout_email, "buyer@example.test")
        self.assertEqual(result.order.delivery_email, "buyer@example.test")

    @override_settings(DEBUG=False)
    def test_service_exception_report_redacts_normalized_emails_and_browser_bearers(self) -> None:
        diagnostic_marker = "checkout-service-diagnostic-marker"
        request = RequestFactory().post("/internal-test/")
        request.COOKIES["findme_cart"] = self.cart_token
        request.COOKIES["findme_purchase"] = self.existing_purchase_token

        with (
            self.purchasable(self.first_photo),
            patch(
                "commerce.checkout._create_order_and_attempt",
                side_effect=RuntimeError(diagnostic_marker),
            ),
        ):
            try:
                create_checkout(
                    event=self.event,
                    cart_browser_token=self.cart_token,
                    purchase_browser_token=self.existing_purchase_token,
                    checkout_email=DIAGNOSTIC_CHECKOUT_EMAIL,
                    checkout_email_confirmation=DIAGNOSTIC_NORMALIZED_EMAIL,
                    watermarked_previews_enabled=True,
                    purchase_enabled=True,
                    adapter_key="deterministic-test",
                    gateway=self.gateway(),
                    return_url_for_order=lambda public_number: (
                        f"https://findme.test/orders/{public_number}/return/"
                    ),
                    now=self.now,
                )
            except RuntimeError as error:
                response = technical_500_response(
                    request,
                    type(error),
                    error,
                    error.__traceback__,
                )
            else:
                self.fail("The injected checkout diagnostic exception was not raised.")

        report = response.content.decode(response.charset)
        self.assertNotIn(DIAGNOSTIC_CHECKOUT_EMAIL, report)
        self.assertNotIn(DIAGNOSTIC_NORMALIZED_EMAIL, report)
        self.assertNotIn(self.cart_token, report)
        self.assertNotIn(self.existing_purchase_token, report)
        self.assertIn(diagnostic_marker, report)

    def test_checkout_snapshots_one_event_exact_price_grant_request_and_no_entitlement_work(
        self,
    ) -> None:
        """A mixed Event, live price, or early email would change the purchased obligation."""
        CartItem.objects.create(cart=self.cart, photo=self.second_photo)
        gateway = self.gateway()

        with self.purchasable(self.first_photo, self.second_photo):
            result = self.checkout(gateway=gateway, purchase_token=self.existing_purchase_token)

        result.order.refresh_from_db()
        result.payment_attempt.refresh_from_db()
        self.assertEqual(result.order.event, self.event)
        self.assertEqual(result.order.total_kopecks, 60000)
        self.assertEqual(result.order.currency, "RUB")
        self.assertEqual(result.order.status, Order.Status.PENDING)
        self.assertEqual(
            set(
                result.order.items.values_list(
                    "photo_id", "photo_public_id", "unit_price_kopecks", "line_total_kopecks"
                )
            ),
            {
                (self.first_photo.pk, self.first_photo.pk, 30000, 30000),
                (self.second_photo.pk, self.second_photo.pk, 30000, 30000),
            },
        )
        self.assertEqual(result.order.originating_cart_token_sha256, self.cart.browser_token_sha256)
        self.assertNotEqual(
            result.order.purchase_browser_token_sha256,
            result.order.originating_cart_token_sha256,
        )
        self.assertEqual(result.payment_attempt.amount_kopecks, 60000)
        self.assertEqual(result.payment_attempt.currency, "RUB")
        self.assertEqual(result.payment_attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(
            result.payment_attempt.reconciliation_next_attempt_at,
            result.payment_attempt.expires_at,
        )
        self.assertEqual(result.confirmation_url, result.payment_attempt.confirmation_url)
        self.assertEqual(OrderAccessGrant.objects.get(order=result.order).source, "checkout")
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertIsNotNone(result.purchase_browser_capability)
        self.assertTrue(result.set_purchase_browser_cookie)

        [request] = gateway.requests
        self.assertEqual(request.order_public_number, result.order.public_number)
        self.assertEqual(request.amount_kopecks, 60000)
        self.assertEqual(request.currency, "RUB")
        self.assertEqual(request.checkout_email, "buyer@example.test")
        self.assertEqual(request.idempotency_key, result.payment_attempt.idempotency_key)
        self.assertEqual(request.return_url, result.return_url)
        self.assertEqual(sum(line.line_total_kopecks for line in request.receipt_lines), 60000)
        self.assertTrue(all(line.quantity == 1 for line in request.receipt_lines))
        self.assertTrue(
            all("personal non-commercial use" in line.description for line in request.receipt_lines)
        )

    def test_external_create_runs_after_commit_with_the_persisted_idempotency_key(self) -> None:
        """Provider I/O in the checkout transaction could hold locks or lose retry identity."""
        gateway = self.gateway()

        with self.purchasable(self.first_photo):
            result = self.checkout(gateway=gateway)

        self.assertEqual(gateway.in_atomic_blocks, [False])
        self.assertEqual(gateway.persisted_attempt_ids, [result.payment_attempt.pk])
        self.assertEqual(
            gateway.requests[0].idempotency_key,
            result.payment_attempt.idempotency_key,
        )

    def test_reconciliation_uses_order_then_attempt_locks_without_deadlock(self) -> None:
        """A joined Attempt-rooted lock can invert the canonical Order-then-Attempt sequence."""
        gateway = ReconciliationRaceGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )

        with self.purchasable(self.first_photo), ThreadPoolExecutor(max_workers=2) as executor:
            checkout_future = executor.submit(self.run_checkout, gateway)
            self.assertTrue(gateway.response_created.wait(timeout=2))
            order = Order.objects.get()
            attempt = PaymentAttempt.objects.get()
            competing_future = executor.submit(
                self.lock_order_then_attempt_while_reconciliation_waits,
                gateway,
                order.pk,
                attempt.pk,
            )

            competing_future.result(timeout=5)
            result = checkout_future.result(timeout=5)

        self.assertEqual(result.order.pk, order.pk)
        self.assertEqual(result.payment_attempt.pk, attempt.pk)
        self.assertTrue(result.payment_attempt.provider_payment_id)

    def test_create_timeout_reuses_the_persisted_attempt_and_reconciles_on_retry(self) -> None:
        """A timeout after provider creation must not create a second Order or attempt."""
        gateway = TimeoutOnceGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )

        with (
            self.purchasable(self.first_photo),
            self.assertRaises(CheckoutPaymentUnavailable) as timeout,
        ):
            self.checkout(gateway=gateway)
        first_attempt = PaymentAttempt.objects.get()
        self.assertEqual(first_attempt.provider_payment_id, "")
        self.assertTrue(timeout.exception.set_purchase_browser_cookie)
        self.assertIsNotNone(timeout.exception.purchase_browser_capability)

        with self.purchasable(self.first_photo):
            retried = self.checkout(
                gateway=gateway,
                purchase_token=timeout.exception.purchase_browser_capability.token,
            )

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(retried.payment_attempt.pk, first_attempt.pk)
        self.assertEqual(
            {request.idempotency_key for request in gateway.requests},
            {first_attempt.idempotency_key},
        )
        self.assertTrue(retried.payment_attempt.provider_payment_id)

    def test_active_attempt_keeps_the_original_snapshot_current_even_if_cart_was_mutated(
        self,
    ) -> None:
        """A second immutable Order while payment is active could charge overlapping snapshots."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo, self.second_photo):
            first = self.checkout(gateway=gateway)

        with self.cart_purchasable(self.first_photo, self.second_photo):
            blocked_add = set_photo_selected(
                event=self.event,
                photo_id=self.second_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        with self.cart_purchasable():
            blocked_prune = read_cart(
                event=self.event,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        blocked_clear = clear_cart(event=self.event, browser_token=self.cart_token)

        self.assertFalse(blocked_add.changed)
        self.assertTrue(blocked_add.snapshot.mutation_locked)
        self.assertTrue(blocked_prune.mutation_locked)
        self.assertTrue(blocked_clear.snapshot.mutation_locked)
        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertEqual(
            set(CartItem.objects.filter(cart=self.cart).values_list("photo_id", flat=True)),
            {self.first_photo.pk},
        )

        with self.purchasable(self.first_photo, self.second_photo):
            repeated = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        self.assertEqual(repeated.order.pk, first.order.pk)
        self.assertEqual(repeated.payment_attempt.pk, first.payment_attempt.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(
            set(first.order.items.values_list("photo_id", flat=True)),
            {self.first_photo.pk},
        )

    def test_matching_active_retry_preserves_an_expired_originating_cart(self) -> None:
        """Checking expiry before the active attempt would delete its locked source cart."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        Cart.objects.filter(pk=self.cart.pk).update(expires_at=self.now - timedelta(seconds=1))

        with self.purchasable():
            repeated = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        self.assertEqual(repeated.order.pk, first.order.pk)
        self.assertEqual(repeated.payment_attempt.pk, first.payment_attempt.pk)
        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertTrue(
            CartItem.objects.filter(cart_id=self.cart.pk, photo=self.first_photo).exists()
        )
        self.assertEqual(len(gateway.requests), 1)

    def test_matching_active_retry_preserves_a_now_ineligible_originating_item(self) -> None:
        """Catalog revalidation must not rewrite a snapshot with a payable active attempt."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)

        with self.purchasable():
            repeated = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        self.assertEqual(repeated.order.pk, first.order.pk)
        self.assertEqual(repeated.payment_attempt.pk, first.payment_attempt.pk)
        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertTrue(
            CartItem.objects.filter(cart_id=self.cart.pk, photo=self.first_photo).exists()
        )
        self.assertEqual(len(gateway.requests), 1)

    def test_unauthorized_active_retry_cannot_prune_the_originating_cart(self) -> None:
        """Authority checked after pruning would let a foreign browser mutate the active cart."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)

        for token in (None, self.existing_purchase_token):
            with self.subTest(token=token):
                cart, _ = Cart.objects.get_or_create(
                    event=self.event,
                    browser_token_sha256=self.cart.browser_token_sha256,
                    defaults={"expires_at": self.now + timedelta(days=1)},
                )
                CartItem.objects.get_or_create(cart=cart, photo=self.first_photo)
                denied = None
                try:
                    with self.purchasable():
                        self.checkout(gateway=gateway, purchase_token=token)
                except (CheckoutEmptyCart, CheckoutPaymentUnavailable) as error:
                    denied = error

                self.assertTrue(Cart.objects.filter(pk=cart.pk).exists())
                self.assertTrue(CartItem.objects.filter(cart=cart, photo=self.first_photo).exists())
                self.assertIsInstance(denied, CheckoutPaymentUnavailable)

        first.order.refresh_from_db()
        self.assertEqual(first.order.status, Order.Status.PENDING)
        self.assertEqual(len(gateway.requests), 1)

    def test_terminal_attempt_checkout_prune_supersedes_before_removing_the_cart(self) -> None:
        """A checkout-driven prune must not leave its old terminal-attempt Order pending."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.FAILED,
            terminal_at=self.now,
        )

        with (
            self.purchasable(),
            self.assertRaises(CheckoutEmptyCart),
        ):
            self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        first.order.refresh_from_db()
        self.assertEqual(first.order.status, Order.Status.SUPERSEDED)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_terminal_attempt_retries_same_order_only_while_cart_and_email_are_unchanged(
        self,
    ) -> None:
        """A failed provider attempt must not rewrite or duplicate the immutable Order snapshot."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.FAILED,
            terminal_at=self.now,
        )

        with self.purchasable(self.first_photo):
            retried = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        self.assertEqual(retried.order.pk, first.order.pk)
        self.assertNotEqual(retried.payment_attempt.pk, first.payment_attempt.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.filter(order=first.order).count(), 2)
        self.assertFalse(retried.set_purchase_browser_cookie)

    def test_cart_mutation_after_terminal_attempt_supersedes_and_creates_a_new_snapshot(
        self,
    ) -> None:
        """Reusing an old Order after selection changed would charge the wrong positions."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo, self.second_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.FAILED,
            terminal_at=self.now,
        )

        with self.cart_purchasable(self.first_photo, self.second_photo):
            mutation = set_photo_selected(
                event=self.event,
                photo_id=self.second_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        first.order.refresh_from_db()
        self.assertTrue(mutation.changed)
        self.assertEqual(first.order.status, Order.Status.SUPERSEDED)

        with self.purchasable(self.first_photo, self.second_photo):
            second = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        self.assertNotEqual(second.order.pk, first.order.pk)
        self.assertEqual(second.order.status, Order.Status.PENDING)
        self.assertEqual(
            set(second.order.items.values_list("photo_id", flat=True)),
            {
                self.first_photo.pk,
                self.second_photo.pk,
            },
        )
        self.assertEqual(
            second.order.originating_cart_token_sha256,
            first.order.originating_cart_token_sha256,
        )

    def test_read_prune_after_terminal_attempt_supersedes_in_the_prune_transaction(self) -> None:
        """A catalog-driven prune is a mutation and must retire the old payable snapshot."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.FAILED,
            terminal_at=self.now,
        )

        with self.cart_purchasable():
            pruned = read_cart(
                event=self.event,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )

        first.order.refresh_from_db()
        self.assertTrue(pruned.pruned)
        self.assertEqual(first.order.status, Order.Status.SUPERSEDED)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_clear_after_terminal_attempt_supersedes_in_the_clear_transaction(self) -> None:
        """Clearing a retryable cart must retire the exact pending Order immediately."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.CANCELED,
            terminal_at=self.now,
        )

        cleared = clear_cart(event=self.event, browser_token=self.cart_token)

        first.order.refresh_from_db()
        self.assertTrue(cleared.changed)
        self.assertEqual(first.order.status, Order.Status.SUPERSEDED)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())

    def test_paid_history_allows_deliberate_repeat_purchase_of_the_same_photo(self) -> None:
        """Paid history must not convert deliberate checkout into entitlement reuse."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.SUCCEEDED,
            terminal_at=self.now,
        )
        Order.objects.filter(pk=first.order.pk).update(
            status=Order.Status.PAID,
            paid_at=self.now,
        )

        with self.purchasable(self.first_photo):
            repeated = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )

        self.assertNotEqual(repeated.order.pk, first.order.pk)
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(
            OrderItem.objects.filter(photo=self.first_photo).count(),
            2,
        )

    def test_provider_raw_result_is_rejected_and_order_remains_retryable(self) -> None:
        """A provider SDK object must not bypass the normalized adapter boundary."""
        gateway = RawProviderResultGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )

        with self.purchasable(self.first_photo), self.assertRaises(CheckoutPaymentUnavailable):
            self.checkout(gateway=gateway)

        attempt = PaymentAttempt.objects.get()
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(attempt.provider_payment_id, "")
        self.assertEqual(EmailDelivery.objects.count(), 0)

    def test_reconciliation_does_not_write_provider_data_to_a_replaced_attempt(self) -> None:
        """A late create response must not attach itself after another attempt became current."""
        gateway = ReplacedAttemptGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )

        with self.purchasable(self.first_photo), self.assertRaises(CheckoutPaymentUnavailable):
            self.checkout(gateway=gateway)

        stale = PaymentAttempt.objects.exclude(idempotency_key="replacement-current-attempt").get()
        current = PaymentAttempt.objects.get(idempotency_key="replacement-current-attempt")
        self.assertEqual(stale.provider_payment_id, "")
        self.assertEqual(current.provider_payment_id, "")
        self.assertEqual(current.status, PaymentAttempt.Status.PENDING)

    def test_existing_attempt_requires_matching_gateway_identity_before_external_io(self) -> None:
        """Retrying provider-A evidence through provider B would corrupt payment routing."""
        first_gateway = TimeoutOnceGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
            adapter_key="provider-a",
        )
        with (
            self.purchasable(self.first_photo),
            self.assertRaises(CheckoutPaymentUnavailable) as timeout,
        ):
            self.checkout(
                gateway=first_gateway,
                adapter_key="provider-a",
            )

        mismatched_gateway = self.gateway(adapter_key="provider-b")
        with self.purchasable(self.first_photo), self.assertRaises(CheckoutPaymentUnavailable):
            self.checkout(
                gateway=mismatched_gateway,
                adapter_key="provider-a",
                purchase_token=timeout.exception.purchase_browser_capability.token,
            )
        wrong_retry_gateway = self.gateway(adapter_key="provider-b")
        with self.purchasable(self.first_photo), self.assertRaises(CheckoutPaymentUnavailable):
            self.checkout(
                gateway=wrong_retry_gateway,
                adapter_key="provider-b",
                purchase_token=timeout.exception.purchase_browser_capability.token,
            )

        attempt = PaymentAttempt.objects.get()
        self.assertEqual(attempt.adapter_key, "provider-a")
        self.assertEqual(attempt.provider_payment_id, "")
        self.assertEqual(mismatched_gateway.requests, [])
        self.assertEqual(wrong_retry_gateway.requests, [])

    def test_existing_attempt_rejects_missing_or_foreign_purchase_capability(self) -> None:
        """An unauthorized duplicate must not proceed to payment for an inaccessible Order."""
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)

        for token in (None, self.existing_purchase_token):
            with (
                self.subTest(token=token),
                self.purchasable(self.first_photo),
                self.assertRaises(CheckoutPaymentUnavailable),
            ):
                self.checkout(gateway=gateway, purchase_token=token)

        with self.purchasable(self.first_photo):
            authorized = self.checkout(
                gateway=gateway,
                purchase_token=first.purchase_browser_capability.token,
            )
        self.assertEqual(authorized.order.pk, first.order.pk)
        self.assertEqual(len(gateway.requests), 1)
        self.assertTrue(
            purchase_browser_authorizes_order(
                order=authorized.order,
                token=first.purchase_browser_capability.token,
                now=self.now,
            )
        )

    def test_pending_checkout_blocks_a_real_concurrent_public_cart_mutation(self) -> None:
        """A cart command racing provider create must observe the committed pending attempt."""
        gateway = BlockingCreateGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )

        with (
            self.purchasable(self.first_photo),
            self.cart_purchasable(self.first_photo, self.second_photo),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            checkout_future = executor.submit(self.run_checkout, gateway)
            self.assertTrue(gateway.create_started.wait(timeout=2))
            mutation_future = executor.submit(
                set_photo_selected,
                event=self.event,
                photo_id=self.second_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
            mutation = mutation_future.result(timeout=2)
            gateway.release_create.set()
            checkout_future.result(timeout=2)

        self.assertFalse(mutation.changed)
        self.assertTrue(mutation.snapshot.mutation_locked)
        self.assertFalse(CartItem.objects.filter(cart=self.cart, photo=self.second_photo).exists())

    def run_checkout(self, gateway):
        close_old_connections()
        try:
            return create_checkout(
                event=Event.objects.get(pk=self.event.pk),
                cart_browser_token=self.cart_token,
                purchase_browser_token=None,
                checkout_email="buyer@example.test",
                checkout_email_confirmation="buyer@example.test",
                watermarked_previews_enabled=True,
                purchase_enabled=True,
                adapter_key="deterministic-test",
                gateway=gateway,
                return_url_for_order=lambda public_number: (
                    f"https://findme.test/orders/{public_number}/return/"
                ),
                now=self.now,
            )
        finally:
            close_old_connections()

    def lock_order_then_attempt_while_reconciliation_waits(
        self,
        gateway: ReconciliationRaceGateway,
        order_id: int,
        attempt_id: int,
    ) -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order_id)
                gateway.release_response.set()
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT wait_event_type
                            FROM pg_stat_activity
                            WHERE pid = %s
                            """,
                            [gateway.reconciliation_backend_pid],
                        )
                        row = cursor.fetchone()
                    if row == ("Lock",):
                        break
                    sleep(0.01)
                else:
                    raise AssertionError("Reconciliation did not wait for the locked Order.")
                PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        finally:
            close_old_connections()

    def test_equivalent_concurrent_submissions_share_one_order_attempt_and_idempotency_key(
        self,
    ) -> None:
        """An unauthorized double-submit must fail while one request creates the Order."""
        gateway = self.gateway()

        with self.purchasable(self.first_photo), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.run_checkout, gateway) for _ in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except CheckoutPaymentUnavailable as error:
                    outcomes.append(error)

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        accepted = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
        rejected = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(accepted[0].order.pk, Order.objects.get().pk)
        self.assertTrue(accepted[0].set_purchase_browser_cookie)
        self.assertIsNotNone(accepted[0].purchase_browser_capability)
        self.assertTrue(
            purchase_browser_authorizes_order(
                order=accepted[0].order,
                token=accepted[0].purchase_browser_capability.token,
                now=self.now,
            )
        )
        self.assertEqual(
            accepted[0].payment_attempt.pk,
            PaymentAttempt.objects.get().pk,
        )
        self.assertEqual(
            {request.idempotency_key for request in gateway.requests},
            {PaymentAttempt.objects.get().idempotency_key},
        )
