import hmac
import json
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views.debug import technical_500_response
from feature_flags.registry import (
    PAID_EVENTS,
    PAID_PHOTO_CART,
    PAID_PHOTO_PURCHASE,
    PAID_WATERMARKED_PREVIEWS,
    FeatureDefinition,
)
from feature_flags.states import (
    FEATURE_FLAG_OFF,
    FEATURE_FLAG_ON,
    FEATURE_FLAG_STAFF,
    FeatureFlagState,
)
from feature_flags.testing import override_feature_flags
from picflow.models import Event, Photo

from commerce.identity import browser_token_sha256
from commerce.models import Cart, CartItem, Order, PaymentAttempt
from commerce.payment_gateway import PaymentGatewayError, PaymentGatewayErrorCategory
from commerce.test_payment_gateway import DeterministicPaymentGateway, TestPaymentOutcome


class TimeoutOnceGateway(DeterministicPaymentGateway):
    def __init__(self) -> None:
        super().__init__(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-view-test-secret",
        )
        self._timed_out = False

    def create_payment(self, request):
        created = super().create_payment(request)
        if not self._timed_out:
            self._timed_out = True
            raise PaymentGatewayError(PaymentGatewayErrorCategory.UNAVAILABLE)
        return created


class MalformedConfirmationOnceGateway(DeterministicPaymentGateway):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._returned_malformed_url = False

    def create_payment(self, request):
        created = super().create_payment(request)
        if not self._returned_malformed_url:
            self._returned_malformed_url = True
            return replace(created, confirmation_url="https://[malformed")
        return created


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class CheckoutViewTestCase(TestCase):
    cart_token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

    def setUp(self) -> None:
        self.feature_flag_states: dict[FeatureDefinition, FeatureFlagState] = {}
        self.enterContext(override_feature_flags(self.feature_flag_states))
        self.event = Event.objects.create(
            name="Заезд",
            slug="checkout-event",
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 22),
            city="Москва",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photographer = get_user_model().objects.create_user(username="checkout-photographer")
        self.photo = Photo.objects.create(
            id="checkout-photo",
            event=self.event,
            src="",
            original_key="private/checkout-photo.jpg",
            original_filename="checkout-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=self.photographer,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.cart = Cart.objects.create(
            event=self.event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=timezone.now() + timedelta(days=1),
        )
        CartItem.objects.create(cart=self.cart, photo=self.photo)
        self.client.cookies["findme_cart"] = self.cart_token

    def checkout_url(self) -> str:
        return reverse("commerce:checkout", kwargs={"event_slug": self.event.slug})

    def enable(self, *, purchase: FeatureFlagState) -> None:
        self.feature_flag_states.update(
            {
                PAID_EVENTS: FEATURE_FLAG_ON,
                PAID_PHOTO_CART: FEATURE_FLAG_ON,
                PAID_WATERMARKED_PREVIEWS: FEATURE_FLAG_ON,
                PAID_PHOTO_PURCHASE: purchase,
            }
        )

    def purchasable(self):
        patches = ExitStack()
        queryset = Photo.objects.filter(pk=self.photo.pk)
        patches.enter_context(
            patch("commerce.services.purchasable_paid_photo_queryset", return_value=queryset)
        )
        patches.enter_context(
            patch("commerce.views.purchasable_paid_photo_queryset", return_value=queryset)
        )
        patches.enter_context(
            patch("commerce.checkout.purchasable_paid_photo_queryset", return_value=queryset)
        )
        return patches

    def gateway(self) -> DeterministicPaymentGateway:
        return DeterministicPaymentGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-view-test-secret",
        )


class CheckoutRouteGateTests(CheckoutViewTestCase):
    def test_missing_purchase_gate_closes_checkout_without_side_effects(self) -> None:
        response = self.client.get(self.checkout_url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.cookies, {})
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_off_purchase_gate_closes_checkout_without_reading_or_mutating_the_cart(self) -> None:
        self.enable(purchase=FEATURE_FLAG_OFF)

        with patch("commerce.views.read_cart") as read_cart:
            response = self.client.get(self.checkout_url())

        self.assertEqual(response.status_code, 404)
        read_cart.assert_not_called()
        self.assertTrue(CartItem.objects.filter(cart=self.cart, photo=self.photo).exists())

    def test_staff_and_on_purchase_gates_redirect_checkout_get_to_the_cart(self) -> None:
        self.enable(purchase=FEATURE_FLAG_STAFF)
        staff = get_user_model().objects.create_user(username="checkout-staff", is_staff=True)

        with self.purchasable():
            anonymous = self.client.get(self.checkout_url())
            self.client.force_login(staff)
            staff_response = self.client.get(self.checkout_url())
            self.client.logout()
            self.client.cookies["findme_cart"] = self.cart_token
            self.feature_flag_states[PAID_PHOTO_PURCHASE] = FEATURE_FLAG_ON
            public_response = self.client.get(self.checkout_url())

        self.assertEqual(anonymous.status_code, 404)
        for response in (staff_response, public_response):
            self.assertRedirects(
                response,
                reverse("commerce:detail", kwargs={"event_slug": self.event.slug}),
                fetch_redirect_response=False,
            )
            self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_cart_only_offers_checkout_when_the_purchase_gate_allows_the_current_visitor(
        self,
    ) -> None:
        cart_url = reverse("commerce:detail", kwargs={"event_slug": self.event.slug})
        self.enable(purchase=FEATURE_FLAG_OFF)

        with self.purchasable():
            closed = self.client.get(cart_url)
            self.feature_flag_states[PAID_PHOTO_PURCHASE] = FEATURE_FLAG_ON
            opened = self.client.get(cart_url)

        self.assertEqual(closed.status_code, 200)
        self.assertNotContains(closed, "Перейти к оплате")
        self.assertContains(opened, "Электронная почта")
        self.assertContains(opened, "Оплатить 300 ₽")
        self.assertContains(opened, f'action="{self.checkout_url()}"')
        self.assertNotContains(opened, "Перейти к оплате")
        self.assertNotContains(opened, "<dialog")
        self.assertContains(opened, "Вернуться к мероприятию")
        self.assertNotContains(opened, "Продолжить выбор")


class CheckoutSubmissionTests(CheckoutViewTestCase):
    def test_checkout_post_requires_csrf_and_creates_one_normalized_order_with_exact_cookie(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies["findme_cart"] = self.cart_token
        gateway = self.gateway()

        with self.purchasable(), patch("commerce.views._payment_gateway", return_value=gateway):
            rejected = csrf_client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            response = self.client.post(
                self.checkout_url(),
                {"email": " Buyer@EXAMPLE.test "},
            )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://payment.test.invalid/"))
        order = Order.objects.get()
        self.assertEqual(order.checkout_email, "buyer@example.test")
        self.assertEqual(order.delivery_email, "buyer@example.test")
        cookie = response.cookies["findme_purchase"]
        self.assertEqual(int(cookie["max-age"]), 30 * 24 * 60 * 60)
        self.assertEqual(cookie["path"], "/")
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    def test_first_timeout_preserves_the_new_capability_for_one_idempotent_retry(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        gateway = TimeoutOnceGateway()

        with self.purchasable(), patch("commerce.views._payment_gateway", return_value=gateway):
            failed = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            retried = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )

        self.assertEqual(failed.status_code, 200)
        self.assertContains(failed, "Не удалось перейти к оплате. Попробуйте ещё раз.")
        self.assertIn("findme_purchase", failed.cookies)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(retried.status_code, 302)
        self.assertTrue(retried["Location"].startswith("https://payment.test.invalid/"))

    def test_malformed_provider_confirmation_keeps_capability_and_retries_same_attempt(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        malformed_gateway = MalformedConfirmationOnceGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"checkout-view-test-secret",
        )

        with (
            self.purchasable(),
            patch("commerce.views._payment_gateway", return_value=malformed_gateway),
        ):
            failed = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            order = Order.objects.get()
            attempt = PaymentAttempt.objects.get(order=order)
            retried = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )

        self.assertEqual(failed.status_code, 200)
        self.assertIn("findme_purchase", failed.cookies)
        self.assertEqual(attempt.confirmation_url, "")
        self.assertEqual(retried.status_code, 302)
        self.assertTrue(retried["Location"].startswith("https://payment.test.invalid/"))
        self.assertEqual(Order.objects.get().pk, order.pk)
        self.assertEqual(PaymentAttempt.objects.get().pk, attempt.pk)

    def test_active_payment_cart_links_to_its_pending_order_instead_of_another_checkout(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        cart_url = reverse("commerce:detail", kwargs={"event_slug": self.event.slug})

        with (
            self.purchasable(),
            patch("commerce.views._payment_gateway", return_value=self.gateway()),
        ):
            checkout = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            order = Order.objects.get()
            cart = self.client.get(cart_url)

        self.assertEqual(checkout.status_code, 302)
        self.assertEqual(cart.status_code, 200)
        self.assertContains(cart, "Продолжить оплату")
        self.assertContains(
            cart,
            reverse("commerce:order", kwargs={"public_number": order.public_number}),
        )
        self.assertNotContains(cart, "Перейти к оплате")

    def test_locked_cart_hides_pending_continuation_when_purchase_gate_closes(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        cart_url = reverse("commerce:detail", kwargs={"event_slug": self.event.slug})

        with (
            self.purchasable(),
            patch("commerce.views._payment_gateway", return_value=self.gateway()),
        ):
            self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            self.feature_flag_states[PAID_PHOTO_PURCHASE] = FEATURE_FLAG_OFF
            closed = self.client.get(cart_url)
            self.feature_flag_states[PAID_PHOTO_PURCHASE] = FEATURE_FLAG_STAFF
            anonymous_staff = self.client.get(cart_url)
            staff = get_user_model().objects.create_user(
                username="locked-cart-staff", is_staff=True
            )
            self.client.force_login(staff)
            allowed_staff = self.client.get(cart_url)

        self.assertNotContains(closed, "Продолжить оплату")
        self.assertNotContains(anonymous_staff, "Продолжить оплату")
        self.assertContains(allowed_staff, "Продолжить оплату")


class PaymentNotificationViewTests(CheckoutViewTestCase):
    def notification_url(self) -> str:
        return reverse("payment_notification")

    def test_closed_purchase_gate_rejects_provider_input_without_authentication_or_mutation(
        self,
    ) -> None:
        with patch("commerce.views.apply_authenticated_notification") as notification:
            response = Client(enforce_csrf_checks=True).post(
                self.notification_url(),
                b"untrusted",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")
        notification.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_off_purchase_gate_rejects_provider_input_before_adapter_authentication(self) -> None:
        self.enable(purchase=FEATURE_FLAG_OFF)

        with patch("commerce.views._payment_gateway") as gateway:
            response = Client(enforce_csrf_checks=True).post(
                self.notification_url(),
                b"untrusted",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 404)
        gateway.assert_not_called()

    def test_staff_callback_accepts_authenticated_provider_evidence_without_django_session(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_STAFF)
        gateway = self.gateway()
        staff = get_user_model().objects.create_user(username="callback-staff", is_staff=True)
        self.client.force_login(staff)
        with self.purchasable(), patch("commerce.views._payment_gateway", return_value=gateway):
            checkout = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            attempt = PaymentAttempt.objects.get()
            body = json.dumps(
                {
                    "provider_payment_id": attempt.provider_payment_id,
                    "provider_event_id": "staff-test-event",
                    "status": "pending",
                    "amount_kopecks": 30000,
                    "currency": "RUB",
                }
            ).encode()
            signature = hmac.new(b"checkout-view-test-secret", body, sha256).hexdigest()
            callback = Client(enforce_csrf_checks=True).post(
                self.notification_url(),
                body,
                content_type="application/json",
                HTTP_X_TEST_PAYMENT_SIGNATURE=signature,
            )

        self.assertEqual(checkout.status_code, 302)
        self.assertEqual(callback.status_code, 204)

    def test_authenticated_provider_notification_is_csrf_exempt_and_rejects_unverified_input(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        gateway = self.gateway()
        with self.purchasable(), patch("commerce.views._payment_gateway", return_value=gateway):
            checkout = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )
            order = Order.objects.get()
            attempt = PaymentAttempt.objects.get(order=order)
            body = json.dumps(
                {
                    "provider_payment_id": attempt.provider_payment_id,
                    "provider_event_id": "test-event-1",
                    "status": "pending",
                    "amount_kopecks": 30000,
                    "currency": "RUB",
                }
            ).encode()
            invalid = Client(enforce_csrf_checks=True).post(
                self.notification_url(),
                body,
                content_type="application/json",
                HTTP_X_TEST_PAYMENT_SIGNATURE="invalid",
            )
            signature = hmac.new(b"checkout-view-test-secret", body, sha256).hexdigest()
            accepted = Client(enforce_csrf_checks=True).post(
                self.notification_url(),
                body,
                content_type="application/json",
                HTTP_X_TEST_PAYMENT_SIGNATURE=signature,
            )

        order.refresh_from_db()
        self.assertEqual(checkout.status_code, 302)
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(accepted.status_code, 204)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(accepted["Cache-Control"], "private, no-store")
        self.assertEqual(accepted["Referrer-Policy"], "no-referrer")

    @override_settings(DEBUG=False)
    def test_checkout_exception_report_redacts_email_and_purchase_bearers(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        self.client.cookies["findme_purchase"] = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
        reports: list[str] = []
        exception_client = Client(raise_request_exception=False)
        exception_client.cookies["findme_cart"] = self.cart_token
        exception_client.cookies["findme_purchase"] = self.client.cookies["findme_purchase"].value
        exception_client.cookies["ordinary_cookie"] = "visible-diagnostic-cookie"

        def capture_report(request, error):
            response = technical_500_response(
                request,
                type(error),
                error,
                error.__traceback__,
            )
            reports.append(response.content.decode(response.charset))
            return response

        with (
            self.purchasable(),
            patch("commerce.views._payment_gateway", return_value=self.gateway()),
            patch("commerce.views.create_checkout", side_effect=RuntimeError("forced checkout")),
            patch(
                "django.core.handlers.exception.response_for_exception",
                side_effect=capture_report,
            ),
        ):
            response = exception_client.post(
                self.checkout_url(),
                {
                    "email": "Buyer.Secret@example.test",
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertNotIn("Buyer.Secret@example.test", report)
        self.assertNotIn(self.cart_token, report)
        self.assertNotIn(self.client.cookies["findme_purchase"].value, report)
        self.assertIn("ordinary_cookie", report)
        self.assertIn("visible-diagnostic-cookie", report)
        self.assertIn("forced checkout", report)
