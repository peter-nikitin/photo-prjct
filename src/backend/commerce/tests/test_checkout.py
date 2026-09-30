from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event as ThreadEvent
from threading import Lock
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.http import HttpResponse
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone
from django.views.debug import technical_500_response
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event, Photo
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from commerce.capabilities import purchase_browser_authorizes_order
from commerce.checkout import (
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
from commerce.services import clear_cart, read_cart, set_photo_selected, start_new_cart
from commerce.test_payment_gateway import DeterministicPaymentGateway, TestPaymentOutcome
from commerce.views import CART_COOKIE_NAME, _apply_cart_token, _apply_mutation_cookie

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

    def test_exact_order_concurrent_retry_commits_one_attempt_before_gateway_io(self):
        from commerce.order_payment import continue_order_payment

        with self.purchasable(self.first_photo):
            original = self.checkout(purchase_token=self.existing_purchase_token)
        PaymentAttempt.objects.filter(pk=original.payment_attempt.pk).update(
            status=PaymentAttempt.Status.CANCELED,
            terminal_at=self.now,
        )
        gateway = BlockingCreateGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"retry-secret",
        )

        def retry():
            close_old_connections()
            try:
                return continue_order_payment(
                    public_number=original.order.public_number,
                    purchase_browser_token=self.existing_purchase_token,
                    gateway_factory=lambda: gateway,
                    return_url_for_order=lambda number: (
                        f"https://findme.test/orders/{number}/return/"
                    ),
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(retry)
            self.assertTrue(gateway.create_started.wait(timeout=2))
            second = pool.submit(retry)
            try:
                self.assertIsNone(second.result(timeout=2))
            finally:
                gateway.release_create.set()
            self.assertTrue(first.result(timeout=2).startswith("https://"))
        self.assertEqual(original.order.payment_attempts.count(), 2)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(gateway.in_atomic_blocks, [False])
        self.assertEqual(Order.objects.count(), 1)

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

    def make_purchasable_watermarked_photo(self) -> Photo:
        owner = get_user_model().objects.create_user(username="hidden-checkout-owner")
        photo = Photo.objects.create(
            id="hidden-checkout-photo",
            event=self.event,
            src="",
            uploaded_by=owner,
            original_key="originals/hidden-checkout-photo.jpg",
            original_filename="hidden-checkout-photo.jpg",
            original_size=123,
            original_content_type="image/jpeg",
            uploaded_at=self.now,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        configuration = {
            GENERATE_WATERMARKED_PREVIEW_PROCESSOR: {"variant": "preview-watermarked-v1"}
        }
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=self.now,
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=self.now,
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=self.now,
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-watermarked-v1",
            final_key="derivatives/previews/hidden-checkout-photo/preview-watermarked-v1/accepted.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="b" * 64,
            accepted_attempt=attempt,
        )
        photo.gallery_media_projection = publish_gallery_media(derivative)
        return photo

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

    def test_hidden_paid_photo_is_pruned_and_cannot_start_a_new_checkout(self) -> None:
        photo = self.make_purchasable_watermarked_photo()
        self.cart.items.all().delete()
        CartItem.objects.create(cart=self.cart, photo=photo)
        visible = read_cart(
            event=self.event,
            browser_token=self.cart_token,
            watermarked_previews_enabled=True,
            now=self.now,
        )
        self.assertEqual(visible.photo_ids, (photo.pk,))
        photo.is_hidden = True
        photo.save(update_fields=["is_hidden"])

        hidden = read_cart(
            event=self.event,
            browser_token=self.cart_token,
            watermarked_previews_enabled=True,
            now=self.now,
        )

        self.assertEqual(hidden.photo_ids, ())
        self.assertTrue(hidden.pruned)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())
        replacement = Cart.objects.create(
            event=self.event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=self.now + timedelta(days=1),
        )
        CartItem.objects.create(cart=replacement, photo=photo)
        gateway = self.gateway()
        with self.assertRaises(CheckoutEmptyCart):
            self.checkout(gateway=gateway)
        self.assertEqual(gateway.requests, [])
        self.assertFalse(Order.objects.exists())
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_checkout_consumes_cart_and_rotates_other_event_without_extending_expiry(self):
        other = Cart.objects.create(
            event=self.other_event,
            browser_token_sha256=self.cart.browser_token_sha256,
            expires_at=self.now + timedelta(hours=2),
        )
        CartItem.objects.create(cart=other, photo=self.other_photo)
        expiry = other.expires_at
        with self.purchasable(self.first_photo):
            result = self.checkout()
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertNotEqual(result.cart_browser_token, self.cart_token)
        other.refresh_from_db()
        self.assertEqual(
            other.browser_token_sha256, browser_token_sha256(result.cart_browser_token)
        )
        self.assertEqual(other.expires_at, expiry)
        self.assertEqual(
            list(other.items.values_list("photo_id", flat=True)), [self.other_photo.pk]
        )

    def test_bank_failure_carries_consumed_cart_identity_and_order(self):
        gateway = TimeoutOnceGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )
        with (
            self.purchasable(self.first_photo),
            self.assertRaises(CheckoutPaymentUnavailable) as failure,
        ):
            self.checkout(gateway=gateway)
        self.assertFalse(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertNotEqual(failure.exception.cart_browser_token, self.cart_token)
        self.assertEqual(failure.exception.order_public_number, Order.objects.get().public_number)
        self.assertTrue(failure.exception.set_purchase_browser_cookie)

    def test_new_selection_of_same_photo_creates_second_pending_order(self):
        with self.purchasable(self.first_photo):
            first = self.checkout()
        with self.cart_purchasable(self.first_photo):
            selected = set_photo_selected(
                event=self.event,
                photo_id=self.first_photo.pk,
                selected=True,
                browser_token=first.cart_browser_token,
                watermarked_previews_enabled=True,
            )
        self.assertTrue(selected.changed)
        self.cart_token = first.cart_browser_token
        with self.purchasable(self.first_photo):
            second = self.checkout(purchase_token=first.purchase_browser_capability.token)
        first.order.refresh_from_db()
        self.assertEqual(first.order.status, Order.Status.PENDING)
        self.assertNotEqual(first.order.pk, second.order.pk)
        self.assertEqual(OrderItem.objects.filter(photo=self.first_photo).count(), 2)
        self.assertEqual(
            PaymentAttempt.objects.filter(status=PaymentAttempt.Status.PENDING).count(), 2
        )

    def test_checkout_normalizes_the_single_delivery_email(self) -> None:
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

    def test_legacy_terminal_cart_requires_explicit_reset_and_preserves_order(self):
        with self.purchasable(self.first_photo):
            result = self.checkout()
        PaymentAttempt.objects.filter(pk=result.payment_attempt.pk).update(
            status=PaymentAttempt.Status.FAILED, terminal_at=self.now
        )
        legacy = Cart.objects.create(
            event=self.event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=self.now + timedelta(days=1),
        )
        CartItem.objects.create(cart=legacy, photo=self.first_photo)
        with self.cart_purchasable(self.first_photo, self.second_photo):
            blocked = set_photo_selected(
                event=self.event,
                photo_id=self.second_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        self.assertFalse(blocked.changed)
        reset = start_new_cart(event=self.event, browser_token=self.cart_token)
        self.assertTrue(reset.changed)
        self.assertNotEqual(reset.issued_browser_token, self.cart_token)
        result.order.refresh_from_db()
        result.payment_attempt.refresh_from_db()
        self.assertEqual(result.order.status, Order.Status.PENDING)
        self.assertEqual(result.payment_attempt.status, PaymentAttempt.Status.FAILED)
        self.assertEqual(result.order.items.count(), 1)
        self.assertFalse(Cart.objects.filter(pk=legacy.pk).exists())

    def test_paid_legacy_browser_without_cart_can_select_under_fresh_identity(self):
        with self.purchasable(self.first_photo):
            first = self.checkout()
        Order.objects.filter(pk=first.order.pk).update(status=Order.Status.PAID, paid_at=self.now)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.SUCCEEDED, terminal_at=self.now
        )
        with self.cart_purchasable(self.first_photo):
            added = set_photo_selected(
                event=self.event,
                photo_id=self.first_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        self.assertTrue(added.changed)
        self.assertNotEqual(added.issued_browser_token, self.cart_token)
        assert added.issued_browser_token is not None
        self.assertEqual(
            Cart.objects.get().browser_token_sha256,
            browser_token_sha256(added.issued_browser_token),
        )
        with self.purchasable(self.first_photo), self.assertRaises(CheckoutEmptyCart):
            self.checkout(purchase_token=first.purchase_browser_capability.token)
        first.order.refresh_from_db()
        first.payment_attempt.refresh_from_db()
        self.assertEqual(first.order.status, Order.Status.PAID)
        self.assertEqual(first.payment_attempt.status, PaymentAttempt.Status.SUCCEEDED)

    def test_consumed_old_token_cannot_retry_or_issue_cookie_after_terminal_attempt_failure(self):
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            first = self.checkout(gateway=gateway)
        PaymentAttempt.objects.filter(pk=first.payment_attempt.pk).update(
            status=PaymentAttempt.Status.FAILED, terminal_at=self.now
        )
        with self.purchasable(self.first_photo), self.assertRaises(CheckoutEmptyCart):
            self.checkout(gateway=gateway, purchase_token=first.purchase_browser_capability.token)
        with self.cart_purchasable(self.first_photo):
            mutation = set_photo_selected(
                event=self.event,
                photo_id=self.first_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
            )
        self.assertFalse(mutation.changed)
        self.assertIsNone(mutation.issued_browser_token)
        self.assertFalse(
            Cart.objects.filter(browser_token_sha256=browser_token_sha256(self.cart_token)).exists()
        )
        first.order.refresh_from_db()
        self.assertEqual(first.order.status, Order.Status.PENDING)
        self.assertEqual(len(gateway.requests), 1)

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

    def test_checkout_rejects_mismatched_gateway_before_external_io(self):
        gateway = self.gateway(adapter_key="provider-b")
        with self.purchasable(self.first_photo), self.assertRaises(CheckoutPaymentUnavailable):
            self.checkout(gateway=gateway, adapter_key="provider-a")
        self.assertFalse(Order.objects.exists())
        self.assertTrue(Cart.objects.filter(pk=self.cart.pk).exists())
        self.assertEqual(gateway.requests, [])

    def test_delayed_existing_token_add_cannot_restore_consumed_checkout_cookie(self):
        other = Cart.objects.create(
            event=self.other_event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=self.now + timedelta(hours=2),
        )
        CartItem.objects.create(cart=other, photo=self.other_photo)
        with self.cart_purchasable(self.first_photo, self.second_photo):
            mutation = set_photo_selected(
                event=self.event,
                photo_id=self.second_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
                now=self.now,
            )
        delayed_response = _apply_mutation_cookie(HttpResponse(status=302), result=mutation)
        self.assertTrue(mutation.changed)
        with self.purchasable(self.first_photo, self.second_photo):
            checkout = self.checkout()
        self.assert_delayed_cart_response_preserves_checkout(delayed_response, checkout, other)
        self.assertEqual(checkout.order.items.count(), 2)

    def test_delayed_final_clear_cannot_expire_rotated_checkout_cookie(self):
        cleared = clear_cart(event=self.event, browser_token=self.cart_token, now=self.now)
        self.assertTrue(cleared.changed)
        delayed_response = _apply_mutation_cookie(HttpResponse(status=302), result=cleared)
        with self.cart_purchasable(self.first_photo, self.other_photo):
            set_photo_selected(
                event=self.event,
                photo_id=self.first_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
                now=self.now,
            )
            set_photo_selected(
                event=self.other_event,
                photo_id=self.other_photo.pk,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
                now=self.now,
            )
        other = Cart.objects.get(event=self.other_event)
        with self.purchasable(self.first_photo):
            checkout = self.checkout()
        self.assert_delayed_cart_response_preserves_checkout(delayed_response, checkout, other)

    def assert_delayed_cart_response_preserves_checkout(self, delayed_response, checkout, other):
        checkout_response = HttpResponse(status=302)
        _apply_cart_token(checkout_response, token=checkout.cart_browser_token)
        other.refresh_from_db()
        for responses in (
            (checkout_response, delayed_response),
            (delayed_response, checkout_response),
        ):
            final_cookie = self.cart_token
            for response in responses:
                if CART_COOKIE_NAME in response.cookies:
                    final_cookie = response.cookies[CART_COOKIE_NAME].value
            self.assertEqual(final_cookie, checkout.cart_browser_token)
            self.assertEqual(other.browser_token_sha256, browser_token_sha256(final_cookie))
            with self.cart_purchasable(self.other_photo):
                reachable = read_cart(
                    event=self.other_event,
                    browser_token=final_cookie,
                    watermarked_previews_enabled=True,
                    now=self.now,
                )
            self.assertEqual(reachable.photo_ids, (self.other_photo.pk,))
        self.assertNotIn(CART_COOKIE_NAME, delayed_response.cookies)

    def test_pending_checkout_same_event_stale_add_cannot_replace_browser_cookie(self):
        self.assert_pending_checkout_cookie_reachability(event=self.event, photo=self.second_photo)

    def test_pending_checkout_cross_event_stale_add_cannot_replace_browser_cookie(self):
        self.assert_pending_checkout_cookie_reachability(
            event=self.other_event, photo=self.other_photo
        )

    def assert_pending_checkout_cookie_reachability(self, *, event, photo):
        gateway = BlockingCreateGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-test-secret",
            now=self.now,
        )
        other = Cart.objects.create(
            event=self.other_event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=self.now + timedelta(hours=2),
        )
        position = CartItem.objects.create(cart=other, photo=self.other_photo)
        expiry = other.expires_at
        with (
            self.purchasable(self.first_photo),
            self.cart_purchasable(self.first_photo, self.second_photo, self.other_photo),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            checkout_future = executor.submit(self.run_checkout, gateway)
            self.assertTrue(gateway.create_started.wait(timeout=2))
            try:
                mutation_future = executor.submit(self.run_stale_add, event.pk, photo.pk)
                mutation = mutation_future.result(timeout=2)
            finally:
                gateway.release_create.set()
            result = checkout_future.result(timeout=2)
        checkout_response = HttpResponse(status=302)
        _apply_cart_token(checkout_response, token=result.cart_browser_token)
        mutation_response = _apply_mutation_cookie(HttpResponse(status=302), result=mutation)
        self.assertFalse(mutation.changed)
        self.assertIsNone(mutation.issued_browser_token)
        self.assertNotIn(CART_COOKIE_NAME, mutation_response.cookies)
        other.refresh_from_db()
        self.assertEqual(other.expires_at, expiry)
        self.assertTrue(CartItem.objects.filter(pk=position.pk).exists())
        for responses in (
            (checkout_response, mutation_response),
            (mutation_response, checkout_response),
        ):
            browser_cookie = self.cart_token
            for response in responses:
                if CART_COOKIE_NAME in response.cookies:
                    browser_cookie = response.cookies[CART_COOKIE_NAME].value
            self.assertEqual(browser_cookie, result.cart_browser_token)
            self.assertEqual(other.browser_token_sha256, browser_token_sha256(browser_cookie))
            with self.cart_purchasable(self.other_photo):
                reachable = read_cart(
                    event=self.other_event,
                    browser_token=browser_cookie,
                    watermarked_previews_enabled=True,
                    now=self.now,
                )
            self.assertEqual(reachable.photo_ids, (self.other_photo.pk,))
        result.order.refresh_from_db()
        result.payment_attempt.refresh_from_db()
        self.assertEqual(result.order.status, Order.Status.PENDING)
        self.assertEqual(result.payment_attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(result.order.items.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def run_stale_add(self, event_id, photo_id):
        close_old_connections()
        try:
            return set_photo_selected(
                event=Event.objects.get(pk=event_id),
                photo_id=photo_id,
                selected=True,
                browser_token=self.cart_token,
                watermarked_previews_enabled=True,
                now=self.now,
            )
        finally:
            close_old_connections()

    def run_checkout(self, gateway):
        close_old_connections()
        try:
            return create_checkout(
                event=Event.objects.get(pk=self.event.pk),
                cart_browser_token=self.cart_token,
                purchase_browser_token=None,
                checkout_email="buyer@example.test",
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
                except (CheckoutPaymentUnavailable, CheckoutEmptyCart) as error:
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
