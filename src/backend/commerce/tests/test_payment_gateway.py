import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from django.test import SimpleTestCase

from commerce.payment_gateway import (
    CreatedPayment,
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentGateway,
    PaymentGatewayError,
    PaymentGatewayErrorCategory,
    PaymentObservation,
    PaymentReceiptLine,
    PaymentRequest,
)
from commerce.test_payment_gateway import DeterministicPaymentGateway, TestPaymentOutcome


class PaymentGatewayContractTests(SimpleTestCase):
    """The breaks caught here would leak provider details or change payable facts."""

    secret = b"test-notification-secret"
    now = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)

    def request(self, *, idempotency_key: str = "attempt-idempotency-1") -> PaymentRequest:
        return PaymentRequest(
            order_public_number="FM-ABCDEFGH",
            amount_kopecks=60000,
            currency="RUB",
            receipt_lines=(
                PaymentReceiptLine(
                    description="Original photo photo-one for personal non-commercial use",
                    quantity=1,
                    unit_amount_kopecks=30000,
                    line_total_kopecks=30000,
                ),
                PaymentReceiptLine(
                    description="Original photo photo-two for personal non-commercial use",
                    quantity=1,
                    unit_amount_kopecks=30000,
                    line_total_kopecks=30000,
                ),
            ),
            checkout_email="buyer@example.test",
            idempotency_key=idempotency_key,
            return_url="https://findme.test/orders/FM-ABCDEFGH/return/",
        )

    def notification(self, *, result: CreatedPayment, event_id: str) -> IncomingPaymentNotification:
        body = json.dumps(
            {
                "provider_payment_id": result.provider_payment_id,
                "provider_event_id": event_id,
                "status": result.status.value,
                "amount_kopecks": result.amount_kopecks,
                "currency": result.currency,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return IncomingPaymentNotification(
            headers={"X-Test-Payment-Signature": signature},
            body=body,
        )

    def test_protocol_has_only_create_fetch_and_authenticated_notification_operations(self) -> None:
        """Adding provider-shaped operations would make Commerce depend on one bank protocol."""
        operations = {
            name
            for name, value in PaymentGateway.__dict__.items()
            if callable(value) and not name.startswith("_")
        }

        self.assertEqual(
            operations,
            {"create_payment", "fetch_payment", "authenticate_notification"},
        )

    def test_create_is_deterministic_and_preserves_exact_payment_facts(self) -> None:
        """Changing amount, currency, email, or key could create the wrong provider payment."""
        gateway = DeterministicPaymentGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=self.secret,
            now=self.now,
        )
        request = self.request()

        first = gateway.create_payment(request)
        repeated = gateway.create_payment(request)

        self.assertEqual(repeated, first)
        self.assertEqual(first.status, NormalizedPaymentStatus.PENDING)
        self.assertEqual(first.amount_kopecks, 60000)
        self.assertEqual(first.currency, "RUB")
        self.assertEqual(first.expires_at, self.now + timedelta(hours=24))
        self.assertIn(first.provider_payment_id, first.confirmation_url)
        self.assertEqual(
            gateway.fetch_payment(first.provider_payment_id).idempotency_key,
            request.idempotency_key,
        )

    def test_deterministic_adapter_normalizes_success_cancel_and_pending(self) -> None:
        """An outcome mapping error could invent or suppress a terminal provider state."""
        expected = {
            TestPaymentOutcome.SUCCESS: NormalizedPaymentStatus.SUCCEEDED,
            TestPaymentOutcome.CANCEL: NormalizedPaymentStatus.CANCELED,
            TestPaymentOutcome.PENDING: NormalizedPaymentStatus.PENDING,
        }

        for outcome, normalized_status in expected.items():
            with self.subTest(outcome=outcome):
                result = DeterministicPaymentGateway(
                    outcome=outcome,
                    notification_secret=self.secret,
                    now=self.now,
                ).create_payment(self.request(idempotency_key=f"attempt-{outcome.value}"))
                self.assertEqual(result.status, normalized_status)

    def test_fetch_and_authenticated_notification_return_only_normalized_observations(self) -> None:
        """Raw provider callback fields must not escape the adapter into Commerce."""
        gateway = DeterministicPaymentGateway(
            outcome=TestPaymentOutcome.SUCCESS,
            notification_secret=self.secret,
            now=self.now,
        )
        created = gateway.create_payment(self.request())

        fetched = gateway.fetch_payment(created.provider_payment_id)
        notification = gateway.authenticate_notification(
            self.notification(result=created, event_id="provider-event-1")
        )

        self.assertIsInstance(fetched, PaymentObservation)
        self.assertIsInstance(notification, PaymentObservation)
        self.assertEqual(notification.provider_event_id, "provider-event-1")
        self.assertEqual(notification.provider_payment_id, created.provider_payment_id)
        self.assertEqual(notification.status, NormalizedPaymentStatus.SUCCEEDED)
        self.assertEqual(notification.amount_kopecks, 60000)
        self.assertEqual(notification.currency, "RUB")
        self.assertNotIn("headers", notification.__dict__)
        self.assertNotIn("body", notification.__dict__)

    def test_invalid_notification_and_idempotency_conflict_raise_sanitized_errors(self) -> None:
        """Provider secrets or raw bodies in errors would leak through logs and error pages."""
        gateway = DeterministicPaymentGateway(
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=self.secret,
            now=self.now,
        )
        created = gateway.create_payment(self.request())
        raw_secret = "card-secret-4111111111111111"
        invalid = IncomingPaymentNotification(
            headers={"Authorization": raw_secret, "X-Test-Payment-Signature": "forged"},
            body=raw_secret.encode(),
        )

        with self.assertRaises(PaymentGatewayError) as raised:
            gateway.authenticate_notification(invalid)
        self.assertEqual(
            raised.exception.category,
            PaymentGatewayErrorCategory.AUTHENTICATION_FAILED,
        )
        self.assertNotIn(raw_secret, str(raised.exception))
        self.assertNotIn(raw_secret, repr(raised.exception))
        self.assertNotIn(raw_secret, repr(invalid))

        changed = PaymentRequest(
            order_public_number="FM-ABCDEFGH",
            amount_kopecks=30000,
            currency="RUB",
            receipt_lines=(
                PaymentReceiptLine(
                    description="Original photo photo-one for personal non-commercial use",
                    quantity=1,
                    unit_amount_kopecks=30000,
                    line_total_kopecks=30000,
                ),
            ),
            checkout_email="buyer@example.test",
            idempotency_key="attempt-idempotency-1",
            return_url="https://findme.test/orders/FM-ABCDEFGH/return/",
        )
        with self.assertRaises(PaymentGatewayError) as conflict:
            gateway.create_payment(changed)
        self.assertEqual(
            conflict.exception.category,
            PaymentGatewayErrorCategory.IDEMPOTENCY_CONFLICT,
        )
        self.assertEqual(gateway.fetch_payment(created.provider_payment_id).amount_kopecks, 60000)

    def test_dtos_reject_non_rub_mismatched_or_provider_sdk_values(self) -> None:
        """Permissive DTOs would let provider SDK values bypass exact Commerce validation."""
        values = self.request().__dict__
        for replacement in (
            {"currency": "USD"},
            {"amount_kopecks": 1},
            {"idempotency_key": ""},
            {"return_url": "https://findme.test/orders/another-order/return/"},
        ):
            with self.subTest(replacement=replacement), self.assertRaises((TypeError, ValueError)):
                PaymentRequest(**(values | replacement))

        with self.assertRaises((TypeError, ValueError)):
            CreatedPayment(
                provider_payment_id="provider-1",
                status={"provider_sdk_status": "pending"},
                amount_kopecks=60000,
                currency="RUB",
                confirmation_url="https://payment.test.invalid/provider-1",
                expires_at=self.now,
            )
