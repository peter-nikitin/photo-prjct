from urllib.parse import urlsplit

from django.test import override_settings
from django.urls import reverse
from feature_flags.registry import PAID_PHOTO_PAYMENT_SIMULATOR
from feature_flags.states import (
    FEATURE_FLAG_OFF,
    FEATURE_FLAG_ON,
    FEATURE_FLAG_STAFF,
    FeatureFlagState,
)

from commerce.models import EmailDelivery, Order, PaymentAttempt
from commerce.tests.test_checkout_views import CheckoutViewTestCase


@override_settings(DEBUG=True, ALLOWED_HOSTS=["127.0.0.1"])
class PaymentSimulatorFlowTests(CheckoutViewTestCase):
    host = "127.0.0.1"

    def enable_simulator(self, state: FeatureFlagState) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        self.feature_flag_states[PAID_PHOTO_PAYMENT_SIMULATOR] = state

    def submit_checkout(self):
        with self.purchasable():
            return self.client.post(
                self.checkout_url(),
                {
                    "email": "buyer@example.test",
                },
                HTTP_HOST=self.host,
            )

    def test_on_flag_sends_anonymous_checkout_to_same_origin_simulator(self) -> None:
        self.enable_simulator(FEATURE_FLAG_ON)

        response = self.submit_checkout()

        self.assertEqual(response.status_code, 302)
        location = urlsplit(response["Location"])
        self.assertEqual(location.scheme, "http")
        self.assertEqual(location.netloc, self.host)
        self.assertTrue(location.path.startswith("/payments/simulator/"))
        attempt = PaymentAttempt.objects.get()
        self.assertEqual(attempt.adapter_key, "feature-payment-simulator-v1")
        self.assertEqual(attempt.confirmation_url, response["Location"])

    def test_staff_flag_rejects_anonymous_checkout_and_allows_active_staff(self) -> None:
        self.enable_simulator(FEATURE_FLAG_STAFF)

        anonymous = self.submit_checkout()
        self.assertEqual(anonymous.status_code, 200)
        self.assertContains(anonymous, "Не удалось перейти к оплате")
        self.assertEqual(Order.objects.count(), 0)

        self.client.force_login(self.photographer)
        self.photographer.is_staff = True
        self.photographer.save(update_fields=["is_staff"])
        staff = self.submit_checkout()

        self.assertEqual(staff.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)

    def test_off_flag_closes_simulator_without_creating_an_order(self) -> None:
        self.enable_simulator(FEATURE_FLAG_OFF)

        response = self.submit_checkout()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Не удалось перейти к оплате")
        self.assertEqual(Order.objects.count(), 0)

    def test_successful_simulation_fulfills_order_and_queues_email(self) -> None:
        self.enable_simulator(FEATURE_FLAG_ON)
        checkout = self.submit_checkout()
        simulator_path = urlsplit(checkout["Location"]).path

        page = self.client.get(simulator_path, HTTP_HOST=self.host)
        completed = self.client.post(
            simulator_path,
            {"outcome": "succeeded"},
            HTTP_HOST=self.host,
        )

        order = Order.objects.get()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Тестовый банковский экран")
        self.assertContains(page, order.public_number)
        self.assertEqual(completed.status_code, 302)
        self.assertEqual(
            completed["Location"],
            reverse("commerce:order_return", kwargs={"public_number": order.public_number}),
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.filter(order=order).count(), 1)

    def test_simulator_page_preserves_same_origin_for_csrf_form_submission(self) -> None:
        self.enable_simulator(FEATURE_FLAG_ON)
        checkout = self.submit_checkout()
        simulator_path = urlsplit(checkout["Location"]).path

        page = self.client.get(simulator_path, HTTP_HOST=self.host)

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Referrer-Policy"], "same-origin")

    def test_pending_simulation_keeps_order_and_attempt_pending(self) -> None:
        self._assert_non_successful_outcome(
            outcome="pending",
            expected_attempt_status="pending",
        )

    def test_canceled_simulation_keeps_order_pending_and_cancels_attempt(self) -> None:
        self._assert_non_successful_outcome(
            outcome="canceled",
            expected_attempt_status="canceled",
        )

    def test_turning_flag_off_closes_an_existing_simulator_page(self) -> None:
        self.enable_simulator(FEATURE_FLAG_ON)
        checkout = self.submit_checkout()
        simulator_path = urlsplit(checkout["Location"]).path
        self.feature_flag_states[PAID_PHOTO_PAYMENT_SIMULATOR] = FEATURE_FLAG_OFF

        response = self.client.post(
            simulator_path,
            {"outcome": "succeeded"},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Order.objects.get().status, Order.Status.PENDING)

    def test_turning_purchase_off_closes_an_existing_simulator_page(self) -> None:
        self.enable_simulator(FEATURE_FLAG_ON)
        checkout = self.submit_checkout()
        simulator_path = urlsplit(checkout["Location"]).path
        self.enable(purchase=FEATURE_FLAG_OFF)

        response = self.client.post(
            simulator_path,
            {"outcome": "succeeded"},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Order.objects.get().status, Order.Status.PENDING)

    def _assert_non_successful_outcome(
        self,
        *,
        outcome: str,
        expected_attempt_status: str,
    ) -> None:
        self.enable_simulator(FEATURE_FLAG_ON)
        checkout = self.submit_checkout()
        simulator_path = urlsplit(checkout["Location"]).path

        response = self.client.post(
            simulator_path,
            {"outcome": outcome},
            HTTP_HOST=self.host,
        )

        order = Order.objects.get()
        attempt = PaymentAttempt.objects.get(order=order)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(attempt.status, expected_attempt_status)
        self.assertFalse(EmailDelivery.objects.filter(order=order).exists())
