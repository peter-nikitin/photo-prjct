from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from commerce.checkout import CheckoutPaymentUnavailable
from commerce.models import CommerceAttention, EmailDelivery, Order, PaymentAttempt
from commerce.payment_gateway import (
    IncomingPaymentNotification,
    NormalizedPaymentStatus,
    PaymentObservation,
)
from commerce.payments import apply_authenticated_notification, reconcile_payment_attempt
from commerce.tbank_gateway import TBANK_ADAPTER_KEY
from commerce.test_payment_gateway import TestPaymentOutcome
from commerce.tests import test_checkout as checkout_tests
from commerce.tests import test_payments as payment_tests
from commerce.worker import _release_payment_reconciliation_claim, claim_due_payment_reconciliations


class RecoverableGateway(checkout_tests.TimeoutOnceGateway):
    def __init__(self, **kwargs):
        super().__init__(adapter_key=TBANK_ADAPTER_KEY, **kwargs)
        self.recoveries = []

    def recover_payment(self, key):
        self.recoveries.append(key)
        assert not connection.in_atomic_block
        return None


class BankRecordingGateway(checkout_tests.RecordingGateway):
    def recover_payment(self, key):
        return None


class BankCheckoutTests(TransactionTestCase):
    setUp = checkout_tests.CheckoutServiceTests.setUp
    checkout = checkout_tests.CheckoutServiceTests.checkout
    purchasable = checkout_tests.CheckoutServiceTests.purchasable
    cart_token = checkout_tests.CheckoutServiceTests.cart_token

    def test_uncertain_init_is_durable_and_never_repeated(self):
        gateway = RecoverableGateway(
            now=self.now, outcome=TestPaymentOutcome.PENDING, notification_secret=b"test"
        )
        with self.purchasable(self.first_photo):
            with self.assertRaises(CheckoutPaymentUnavailable) as failure:
                self.checkout(gateway=gateway, adapter_key=TBANK_ADAPTER_KEY)
            token = failure.exception.purchase_browser_capability.token
            with self.assertRaises(CheckoutPaymentUnavailable):
                self.checkout(gateway=gateway, adapter_key=TBANK_ADAPTER_KEY, purchase_token=token)
        attempt = PaymentAttempt.objects.get()
        self.assertEqual(len(gateway.requests), 1)
        self.assertIsNotNone(attempt.initiation_started_at)
        self.assertLessEqual(
            attempt.reconciliation_next_attempt_at, self.now + timedelta(minutes=15)
        )
        self.assertTrue(gateway.recoveries)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)

    def test_local_receipt_rejection_can_be_corrected_without_unknown_bank_obligation(self):
        gateway = RecoverableGateway(
            now=self.now, outcome=TestPaymentOutcome.PENDING, notification_secret=b"test"
        )
        with (
            self.purchasable(self.first_photo),
            patch.object(gateway, "create_payment", side_effect=ValueError("invalid receipt")),
        ):
            with self.assertRaises(CheckoutPaymentUnavailable):
                self.checkout(gateway=gateway, adapter_key=TBANK_ADAPTER_KEY)
        attempt = PaymentAttempt.objects.get()
        self.assertEqual(attempt.status, PaymentAttempt.Status.FAILED)
        self.assertEqual(Order.objects.count(), 1)

    def test_concurrent_submission_cannot_repeat_in_flight_init(self):
        gateway = BankRecordingGateway(
            adapter_key=TBANK_ADAPTER_KEY,
            now=self.now,
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"test",
        )
        original = gateway.create_payment
        token = checkout_tests.CheckoutServiceTests.existing_purchase_token

        def during_init(request):
            with self.assertRaises(CheckoutPaymentUnavailable):
                self.checkout(gateway=gateway, adapter_key=TBANK_ADAPTER_KEY, purchase_token=token)
            return original(request)

        with (
            self.purchasable(self.first_photo),
            patch.object(gateway, "create_payment", side_effect=during_init),
        ):
            result = self.checkout(
                gateway=gateway, adapter_key=TBANK_ADAPTER_KEY, purchase_token=token
            )
        self.assertTrue(result.confirmation_url)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_pending_callback_during_init_preserves_hosted_payment_url(self):
        gateway = BankRecordingGateway(
            adapter_key=TBANK_ADAPTER_KEY,
            now=self.now,
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"test",
        )
        original = gateway.create_payment

        def callback_first(request):
            created = original(request)
            attempt = PaymentAttempt.objects.get(idempotency_key=request.idempotency_key)
            observation_gateway = payment_tests._ObservationGateway(
                PaymentObservation(
                    provider_payment_id=created.provider_payment_id,
                    status=NormalizedPaymentStatus.PENDING,
                    amount_kopecks=created.amount_kopecks,
                    currency="RUB",
                    idempotency_key=attempt.idempotency_key,
                )
            )
            observation_gateway.adapter_key = TBANK_ADAPTER_KEY
            apply_authenticated_notification(
                gateway=observation_gateway, notification=IncomingPaymentNotification({}, b"")
            )
            return created

        with (
            self.purchasable(self.first_photo),
            patch.object(gateway, "create_payment", side_effect=callback_first),
        ):
            result = self.checkout(gateway=gateway, adapter_key=TBANK_ADAPTER_KEY)
        self.assertTrue(result.confirmation_url)


class BankTransitionTests(TransactionTestCase):
    attempt: PaymentAttempt
    setUp = payment_tests.PaymentTransitionTests.setUp

    def bank_attempt(self, *, missing_id=False):
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(
            status="failed", terminal_at=self.now
        )
        self.attempt = PaymentAttempt.objects.create(
            order=self.order,
            adapter_key=TBANK_ADAPTER_KEY,
            amount_kopecks=30000,
            idempotency_key="bank-attempt",
            provider_payment_id="" if missing_id else "bank-payment-1",
            reconciliation_next_attempt_at=self.now,
            expires_at=self.now + timedelta(hours=1),
        )

    def gateway_for(self, status=NormalizedPaymentStatus.SUCCEEDED):
        observation = PaymentObservation(
            provider_payment_id="bank-payment-1",
            status=status,
            amount_kopecks=30000,
            currency="RUB",
            idempotency_key=self.attempt.idempotency_key,
        )
        gateway = payment_tests._ObservationGateway(observation)
        gateway.adapter_key = TBANK_ADAPTER_KEY
        return gateway

    def test_notification_binds_missing_id_and_fulfills_once(self):
        self.bank_attempt(missing_id=True)
        gateway = self.gateway_for()
        for _ in range(2):
            apply_authenticated_notification(
                gateway=gateway, notification=IncomingPaymentNotification({}, b""), now=self.now
            )
        self.attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.attempt.provider_payment_id, "bank-payment-1")
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_pending_bank_fetch_never_expires_locally_and_reschedules(self):
        self.bank_attempt()
        future = self.now + timedelta(days=2)
        claim = claim_due_payment_reconciliations(
            now=future, limit=1, adapter_key=TBANK_ADAPTER_KEY
        )[0]
        reconcile_payment_attempt(
            attempt_id=self.attempt.pk,
            gateway=self.gateway_for(NormalizedPaymentStatus.PENDING),
            now=future,
            expected_reconciliation_lease_id=claim.lease_id,
        )
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(
            self.attempt.reconciliation_state, PaymentAttempt.ReconciliationState.PENDING
        )
        self.assertLessEqual(
            self.attempt.reconciliation_next_attempt_at, future + timedelta(minutes=15)
        )

    def test_bank_failure_keeps_polling_after_multiple_retries(self):
        self.bank_attempt()
        for index in range(4):
            now = self.now + timedelta(days=index + 1)
            claim = claim_due_payment_reconciliations(
                now=now, limit=1, adapter_key=TBANK_ADAPTER_KEY
            )[0]
            _release_payment_reconciliation_claim(claim=claim, now=now)
            self.attempt.refresh_from_db()
            self.assertIsNotNone(self.attempt.reconciliation_next_attempt_at)

    def test_bank_callback_works_without_csrf_session_or_open_gate(self):
        self.bank_attempt(missing_id=True)
        with patch("commerce.views._configured_adapter", return_value=self.gateway_for()):
            response = Client(enforce_csrf_checks=True).post(
                reverse("payment_notification"), data="{}", content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_recovery_without_browser_binds_confirmed_charge(self):
        self.bank_attempt(missing_id=True)
        gateway = self.gateway_for()
        gateway.recover_payment = lambda key: gateway.observation
        claim = claim_due_payment_reconciliations(
            now=self.now, limit=1, adapter_key=TBANK_ADAPTER_KEY
        )[0]
        reconcile_payment_attempt(
            attempt_id=self.attempt.pk,
            gateway=gateway,
            now=self.now,
            expected_reconciliation_lease_id=claim.lease_id,
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_signed_bank_notifications_reject_forgery_and_amount_conflict(self):
        import json

        from commerce.tbank_gateway import TBankGateway, sign_payload
        from commerce.tests.test_tbank_gateway import config

        self.bank_attempt(missing_id=True)
        gateway = TBankGateway(config())
        payload = dict(
            TerminalKey="terminal",
            OrderId="fm-bank-attempt",
            PaymentId="12345",
            Amount=30000,
            Status="CONFIRMED",
            Success=True,
            ErrorCode="0",
        )
        with patch("commerce.views._configured_adapter", return_value=gateway):
            for changes in (
                {"Token": "0" * 64},
                {"Amount": 1},
                {"TerminalKey": "other"},
                {"OrderId": "fm-other"},
            ):
                data = payload | changes
                if "Token" not in changes:
                    data["Token"] = sign_payload(data, "secret")
                response = Client(enforce_csrf_checks=True).post(
                    reverse("payment_notification"),
                    data=json.dumps(data),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 404)
                self.assertFalse(EmailDelivery.objects.exists())
            payload["Token"] = sign_payload(payload, "secret")
            for _ in range(2):
                response = self.client.post(
                    reverse("payment_notification"),
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.content, b"OK")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_return_only_fetches_for_authorized_order_and_uses_server_evidence(self):
        from django.http import HttpResponse

        self.bank_attempt()
        with patch(
            "commerce.views._configured_adapter", return_value=self.gateway_for()
        ) as factory:
            response = self.client.get(
                reverse("commerce:order_return", args=[self.order.public_number])
            )
            self.assertEqual(response.status_code, 404)
            factory.assert_not_called()
            with (
                patch("commerce.views._authorized_order", return_value=(self.order, None)),
                patch("commerce.views._render_order", return_value=HttpResponse("order")),
            ):
                response = self.client.get(
                    reverse("commerce:order_return", args=[self.order.public_number])
                )
                self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_refund_callback_preserves_paid_order_and_opens_attention(self):
        import json

        from commerce.tbank_gateway import TBankGateway, sign_payload
        from commerce.tests.test_tbank_gateway import config

        self.bank_attempt(missing_id=True)
        gateway = TBankGateway(config())
        payload = dict(
            TerminalKey="terminal",
            OrderId="fm-bank-attempt",
            PaymentId="12345",
            Amount=30000,
            Status="CONFIRMED",
            Success=True,
            ErrorCode="0",
        )
        with patch("commerce.views._configured_adapter", return_value=gateway):
            payload["Token"] = sign_payload(payload, "secret")
            self.client.post(
                reverse("payment_notification"),
                data=json.dumps(payload),
                content_type="application/json",
            )
            for status in ("REFUNDED", "REVERSED", "UNRECOGNIZED"):
                payload["Status"] = status
                payload["Token"] = sign_payload(payload, "secret")
                response = self.client.post(
                    reverse("payment_notification"),
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 404)
                self.order.refresh_from_db()
                self.assertEqual(self.order.status, Order.Status.PAID)
                self.assertEqual(EmailDelivery.objects.count(), 1)
                self.assertTrue(
                    CommerceAttention.objects.filter(
                        payment_attempt=self.attempt, kind="manual_payment_conflict"
                    ).exists()
                )
