import hashlib
import hmac
import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.states import FEATURE_FLAG_ON, FEATURE_FLAG_STAFF, FeatureFlagState
from feature_flags.testing import override_feature_flags
from ingestion.storage import ObjectMissing
from picflow.models import Event, Photo
from selfie_search.models import SelfieSearch, SelfieSearchResult

from commerce.capabilities import (
    create_order_access_grant,
    revoke_order_access_grant,
    sign_order_access_grant,
)
from commerce.delivery import (
    claim_due_email_deliveries,
    correct_delivery_email,
    resend_order_access,
    send_claimed_email_delivery,
)
from commerce.email_sender import EmailSendOutcome
from commerce.identity import browser_token_sha256, generate_browser_token
from commerce.models import (
    Cart,
    CartItem,
    CommerceAttention,
    DownloadGrantAudit,
    EmailDelivery,
    Order,
    OrderItem,
    PaymentAttempt,
)
from commerce.original_delivery import PurchasedOriginalUnavailable, sign_purchased_original
from commerce.payment_gateway import NormalizedPaymentStatus, PaymentObservation
from commerce.payments import (
    apply_payment_observation,
    cancel_order,
    mark_order_paid_manually,
    reconcile_payment_attempt,
)
from commerce.services import clear_cart
from commerce.test_email_sender import DeterministicEmailSender
from commerce.test_payment_gateway import DeterministicPaymentGateway, TestPaymentOutcome


class _ObservationGateway:
    adapter_key = "deterministic-test"

    def __init__(self, observation: PaymentObservation) -> None:
        self.observation = observation

    def create_payment(self, request):  # pragma: no cover - reconciliation never creates a payment.
        raise AssertionError("Reconciliation must not create a payment.")

    def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
        if provider_payment_id != self.observation.provider_payment_id:
            raise AssertionError("Reconciliation requested an unrelated payment.")
        return self.observation

    def authenticate_notification(self, notification):  # pragma: no cover - not a callback adapter.
        raise AssertionError("Reconciliation must not authenticate a callback.")


@override_settings(
    COMMERCE_ORDER_ACCESS_SIGNING_SECRET="paid-photo-flow-signing-secret",
    COMMERCE_SUPPORT_CONTACT="support@example.test",
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class PaidPhotoPurchaseFlowTests(TestCase):
    """Breaks here mean reviewed Commerce pieces no longer form a safe customer purchase flow."""

    cart_token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    notification_secret = b"paid-photo-flow-notification-secret"

    def setUp(self) -> None:
        self.feature_flag_states: dict[str, FeatureFlagState] = {}
        self.enterContext(override_feature_flags(self.feature_flag_states))
        self.feature_flag_states.update(
            {
                "paid-events": FEATURE_FLAG_ON,
                "paid-photo-cart": FEATURE_FLAG_ON,
                "paid-watermarked-previews": FEATURE_FLAG_ON,
                "paid-photo-purchase": FEATURE_FLAG_ON,
            }
        )
        self.photographer = get_user_model().objects.create_user(username="flow-photographer")
        self.event = Event.objects.create(
            name="Flow event",
            slug="paid-photo-flow",
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 22),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="flow-photo",
            event=self.event,
            src="",
            original_key="private/flow-photo.jpg",
            original_filename="flow-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=self.photographer,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        cart = Cart.objects.create(
            event=self.event,
            browser_token_sha256=browser_token_sha256(self.cart_token),
            expires_at=timezone.now() + timedelta(days=1),
        )
        CartItem.objects.create(cart=cart, photo=self.photo)
        self.client.cookies["findme_cart"] = self.cart_token

    def purchasable(self):
        queryset = Photo.objects.filter(pk=self.photo.pk)
        return patch.multiple(
            "commerce.views",
            purchasable_paid_photo_queryset=Mock(return_value=queryset),
        )

    def checkout_url(self) -> str:
        return reverse("commerce:checkout", kwargs={"event_slug": self.event.slug})

    def make_order(
        self,
        *,
        status: str = "pending",
        photo: Photo | None = None,
        purchase_browser_token: str | None = None,
    ) -> Order:
        origin_token = generate_browser_token()
        purchased_photo = photo or self.photo
        order = Order.objects.create(
            event=self.event,
            originating_cart_token_sha256=browser_token_sha256(origin_token),
            purchase_browser_token_sha256=browser_token_sha256(
                purchase_browser_token or generate_browser_token()
            ),
            checkout_email="buyer@example.test",
            delivery_email="buyer@example.test",
            total_kopecks=30000,
            status=status,
            paid_at=timezone.now() if status == "paid" else None,
        )
        OrderItem.objects.create(
            order=order,
            photo=purchased_photo,
            photo_public_id=purchased_photo.pk,
            unit_price_kopecks=30000,
            line_total_kopecks=30000,
        )
        create_order_access_grant(order=order, source="checkout")
        return order

    def test_order_a_capabilities_cannot_authorize_order_b_or_its_original(self) -> None:
        """A browser bearer or signed grant must remain scoped to its exact paid Order."""
        order_a_token = generate_browser_token()
        order_a = self.make_order(status="paid", purchase_browser_token=order_a_token)
        order_b_photo = Photo.objects.create(
            id="flow-second-order-photo",
            event=self.event,
            src="",
            original_key="private/flow-second-order-photo.jpg",
            original_filename="flow-second-order-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=self.photographer,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        order_b = self.make_order(status="paid", photo=order_b_photo)
        order_a_grant = order_a.access_grants.get(source="checkout")
        order_a_signature = sign_order_access_grant(
            grant=order_a_grant,
            signing_secret="paid-photo-flow-signing-secret",
        )

        order_b_url = reverse("commerce:order", kwargs={"public_number": order_b.public_number})
        order_b_download_url = reverse(
            "commerce:order_download",
            kwargs={"public_number": order_b.public_number, "photo_id": order_b_photo.pk},
        )
        order_b_grant_url = reverse(
            "commerce:grant_order",
            kwargs={
                "public_number": order_b.public_number,
                "grant_identifier": order_a_grant.pk,
                "signature": order_a_signature,
            },
        )
        order_b_grant_download_url = reverse(
            "commerce:grant_order_download",
            kwargs={
                "public_number": order_b.public_number,
                "grant_identifier": order_a_grant.pk,
                "signature": order_a_signature,
                "photo_id": order_b_photo.pk,
            },
        )
        bearer_client = Client()
        bearer_client.cookies["findme_purchase"] = order_a_token
        storage = Mock()
        with patch("commerce.views._purchased_original_storage", return_value=storage):
            browser_order = bearer_client.get(order_b_url)
            browser_download = bearer_client.get(order_b_download_url)
            grant_order = Client().get(order_b_grant_url)
            grant_download = Client().get(order_b_grant_download_url)

        self.assertEqual(browser_order.status_code, 404)
        self.assertEqual(browser_download.status_code, 404)
        self.assertEqual(grant_order.status_code, 404)
        self.assertEqual(grant_download.status_code, 404)
        storage.sign_final.assert_not_called()
        self.assertFalse(DownloadGrantAudit.objects.filter(order_item__order=order_b).exists())
        order_b.refresh_from_db()
        self.assertIsNone(order_b.first_customer_access_at)

    def test_revoking_one_parallel_grant_denies_only_that_grant_on_real_order_routes(self) -> None:
        """Canonical grant revocation must not invalidate a sibling active Order capability."""
        order = self.make_order(status="paid")
        revoked_grant = order.access_grants.get(source="checkout")
        active_grant = create_order_access_grant(order=order, source="resend")
        revoked_signature = sign_order_access_grant(
            grant=revoked_grant,
            signing_secret="paid-photo-flow-signing-secret",
        )
        active_signature = sign_order_access_grant(
            grant=active_grant,
            signing_secret="paid-photo-flow-signing-secret",
        )

        def grant_order_url(grant, signature: str) -> str:
            return reverse(
                "commerce:grant_order",
                kwargs={
                    "public_number": order.public_number,
                    "grant_identifier": grant.pk,
                    "signature": signature,
                },
            )

        def grant_download_url(grant, signature: str) -> str:
            return reverse(
                "commerce:grant_order_download",
                kwargs={
                    "public_number": order.public_number,
                    "grant_identifier": grant.pk,
                    "signature": signature,
                    "photo_id": self.photo.pk,
                },
            )

        revoked_client = Client()
        active_client = Client()
        revoked_before_revocation = revoked_client.get(
            grant_order_url(revoked_grant, revoked_signature)
        )
        active_before_revocation = active_client.get(
            grant_order_url(active_grant, active_signature)
        )
        self.assertEqual(revoked_before_revocation.status_code, 200)
        self.assertEqual(active_before_revocation.status_code, 200)

        revoke_order_access_grant(revoked_grant)
        storage = Mock()
        storage.sign_final.return_value = "https://storage.test.invalid/parallel-grant-original"
        with patch("commerce.views._purchased_original_storage", return_value=storage):
            revoked_order = revoked_client.get(grant_order_url(revoked_grant, revoked_signature))
            revoked_download = revoked_client.get(
                grant_download_url(revoked_grant, revoked_signature)
            )
            active_order = active_client.get(grant_order_url(active_grant, active_signature))
            active_download = active_client.get(grant_download_url(active_grant, active_signature))

        self.assertEqual(revoked_order.status_code, 404)
        self.assertEqual(revoked_download.status_code, 404)
        self.assertEqual(active_order.status_code, 200)
        self.assertEqual(active_download.status_code, 302)
        self.assertEqual(
            active_download["Location"], "https://storage.test.invalid/parallel-grant-original"
        )
        storage.sign_final.assert_called_once_with(
            key="private/flow-photo.jpg",
            attachment_filename="findme-photo-flow-photo.jpg",
        )
        self.assertEqual(DownloadGrantAudit.objects.filter(order_item__order=order).count(), 1)

    def make_attempt(
        self,
        *,
        order: Order,
        suffix: str,
        expires_at: datetime | None = None,
        reconciliation_next_attempt_at: datetime | None = None,
    ) -> PaymentAttempt:
        return PaymentAttempt.objects.create(
            order=order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="deterministic-test",
            idempotency_key=f"paid-photo-flow-{suffix}",
            provider_payment_id=f"provider-paid-photo-flow-{suffix}",
            expires_at=expires_at or timezone.now() + timedelta(hours=1),
            reconciliation_next_attempt_at=reconciliation_next_attempt_at,
        )

    def succeeded_observation(self, attempt: PaymentAttempt) -> PaymentObservation:
        return PaymentObservation(
            provider_payment_id=attempt.provider_payment_id,
            status=NormalizedPaymentStatus.SUCCEEDED,
            amount_kopecks=attempt.amount_kopecks,
            currency="RUB",
            idempotency_key=attempt.idempotency_key,
            provider_event_id=f"paid-photo-flow-{attempt.pk}",
        )

    def payment_notification(self, *, order: Order, gateway: DeterministicPaymentGateway):
        attempt = order.payment_attempts.get()
        body = json.dumps(
            {
                "provider_payment_id": attempt.provider_payment_id,
                "provider_event_id": "paid-photo-flow-success",
                "status": "succeeded",
                "amount_kopecks": attempt.amount_kopecks,
                "currency": attempt.currency,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return self.client.post(
            reverse("payment_notification"),
            body,
            content_type="application/json",
            HTTP_X_TEST_PAYMENT_SIGNATURE=hmac.new(
                self.notification_secret, body, hashlib.sha256
            ).hexdigest(),
        )

    def test_checkout_to_authenticated_payment_to_email_grant_and_exact_original(self) -> None:
        """Removing any checkout, callback, grant, or item check could expose an unpaid original."""
        gateway = DeterministicPaymentGateway(
            outcome=TestPaymentOutcome.SUCCESS,
            notification_secret=self.notification_secret,
        )
        queryset = Photo.objects.filter(pk=self.photo.pk)
        with (
            patch("commerce.services.purchasable_paid_photo_queryset", return_value=queryset),
            patch("commerce.views.purchasable_paid_photo_queryset", return_value=queryset),
            patch("commerce.checkout.purchasable_paid_photo_queryset", return_value=queryset),
            patch("commerce.views._payment_gateway", return_value=gateway),
        ):
            checkout = self.client.post(
                self.checkout_url(),
                {"email": "buyer@example.test"},
            )

        self.assertEqual(checkout.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(
            self.client.get(
                reverse(
                    "commerce:order_download",
                    kwargs={"public_number": order.public_number, "photo_id": self.photo.pk},
                )
            ).status_code,
            404,
        )
        self.assertEqual(DownloadGrantAudit.objects.count(), 0)

        returned = self.client.get(
            reverse("commerce:order_return", kwargs={"public_number": order.public_number})
        )
        order.refresh_from_db()
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(order.status, Order.Status.PENDING)

        with patch("commerce.views._payment_gateway", return_value=gateway):
            callback = self.payment_notification(order=order, gateway=gateway)
        order.refresh_from_db()
        self.assertEqual(callback.status_code, 204)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(EmailDelivery.objects.filter(order=order).count(), 1)

        immediate_access = self.client.get(
            reverse("commerce:order", kwargs={"public_number": order.public_number})
        )
        order.refresh_from_db()
        self.assertEqual(immediate_access.status_code, 200)
        self.assertIsNotNone(order.first_customer_access_at)

        watermark_resolver = Mock()
        watermark_resolver.resolve_signed.return_value = (
            "https://storage.test.invalid/flow-watermark"
        )
        with patch(
            "commerce.views._purchased_watermarked_media_resolver",
            return_value=watermark_resolver,
        ):
            watermarked_media = self.client.get(
                reverse(
                    "commerce:order_media",
                    kwargs={
                        "public_number": order.public_number,
                        "photo_id": self.photo.pk,
                        "variant": "preview-small",
                    },
                )
            )
        self.assertEqual(watermarked_media.status_code, 302)
        self.assertEqual(
            watermarked_media["Location"], "https://storage.test.invalid/flow-watermark"
        )

        sender = DeterministicEmailSender()
        claim = claim_due_email_deliveries()[0]
        delivery = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret="paid-photo-flow-signing-secret",
            order_access_url_for_grant=lambda grant, signature: (
                "https://testserver"
                + reverse(
                    "commerce:grant_order",
                    kwargs={
                        "public_number": grant.order.public_number,
                        "grant_identifier": grant.pk,
                        "signature": signature,
                    },
                )
            ),
            support_contact="support@example.test",
            timeout_seconds=20,
        )
        assert delivery is not None
        self.assertEqual(delivery.state, EmailDelivery.State.SUCCEEDED)
        self.assertEqual(len(sender.captured_messages), 1)
        access_url = next(
            line
            for line in sender.captured_messages[0].text_body.splitlines()
            if line.startswith("https://")
        )

        grant_client = Client()
        grant_path = urlsplit(access_url).path
        self.assertEqual(grant_client.get(grant_path).status_code, 200)
        storage = Mock()
        storage.sign_final.return_value = "https://storage.test.invalid/flow-original"
        grant = EmailDelivery.objects.get(pk=delivery.pk).access_grant
        signature = grant_path.split("/")[5]
        download_path = reverse(
            "commerce:grant_order_download",
            kwargs={
                "public_number": order.public_number,
                "grant_identifier": grant.pk,
                "signature": signature,
                "photo_id": self.photo.pk,
            },
        )
        with patch("commerce.views._purchased_original_storage", return_value=storage):
            download = grant_client.get(download_path)
            cross_item = grant_client.get(download_path.replace(self.photo.pk, "foreign-photo"))
        self.assertEqual(download.status_code, 302)
        self.assertEqual(download["Location"], "https://storage.test.invalid/flow-original")
        self.assertEqual(cross_item.status_code, 404)
        storage.sign_final.assert_called_once_with(
            key="private/flow-photo.jpg",
            attachment_filename="findme-photo-flow-photo.jpg",
        )

    def test_canceled_pending_superseded_mismatched_and_manual_payment_transitions(self) -> None:
        """Wrong transition handling could lose real money or grant originals for a mismatch."""
        operator = get_user_model().objects.create_user(username="flow-operator", is_staff=True)
        canceled = self.make_order()
        canceled_attempt = self.make_attempt(order=canceled, suffix="canceled")
        cancel_order(order_id=canceled.pk, actor=operator)
        apply_payment_observation(
            attempt_id=canceled_attempt.pk,
            adapter_key="deterministic-test",
            source="notification",
            observation=self.succeeded_observation(canceled_attempt),
        )
        canceled.refresh_from_db()
        self.assertEqual(canceled.status, Order.Status.CANCELED)
        self.assertTrue(
            CommerceAttention.objects.filter(
                kind="payment_mismatch", payment_attempt=canceled_attempt
            ).exists()
        )

        superseded = self.make_order(status="superseded")
        superseded_attempt = self.make_attempt(order=superseded, suffix="late-success")
        apply_payment_observation(
            attempt_id=superseded_attempt.pk,
            adapter_key="deterministic-test",
            source="status_fetch",
            observation=self.succeeded_observation(superseded_attempt),
        )
        superseded.refresh_from_db()
        self.assertEqual(superseded.status, Order.Status.PAID)
        self.assertTrue(EmailDelivery.objects.filter(order=superseded).exists())

        mismatched = self.make_order()
        mismatched_attempt = self.make_attempt(order=mismatched, suffix="mismatch")
        apply_payment_observation(
            attempt_id=mismatched_attempt.pk,
            adapter_key="deterministic-test",
            source="notification",
            observation=PaymentObservation(
                provider_payment_id=mismatched_attempt.provider_payment_id,
                status=NormalizedPaymentStatus.SUCCEEDED,
                amount_kopecks=1,
                currency="RUB",
                idempotency_key=mismatched_attempt.idempotency_key,
                provider_event_id="paid-photo-flow-mismatch",
            ),
        )
        mismatched.refresh_from_db()
        self.assertEqual(mismatched.status, Order.Status.PENDING)
        self.assertFalse(EmailDelivery.objects.filter(order=mismatched).exists())
        self.assertTrue(
            CommerceAttention.objects.filter(
                kind="payment_mismatch", payment_attempt=mismatched_attempt
            ).exists()
        )

        manual = self.make_order()
        mark_order_paid_manually(order_id=manual.pk, actor=operator)
        manual.refresh_from_db()
        self.assertEqual(manual.status, Order.Status.PAID)
        self.assertTrue(EmailDelivery.objects.filter(order=manual).exists())

    def test_pending_expiry_reconciles_without_inventing_success(self) -> None:
        """Skipping the authoritative fetch at expiry could falsely expire a paid bank payment."""
        order = self.make_order()
        expired_at = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
        attempt = self.make_attempt(
            order=order,
            suffix="pending-expiry",
            expires_at=expired_at - timedelta(seconds=1),
            reconciliation_next_attempt_at=expired_at - timedelta(seconds=1),
        )
        reconcile_payment_attempt(
            attempt_id=attempt.pk,
            gateway=_ObservationGateway(
                PaymentObservation(
                    provider_payment_id=attempt.provider_payment_id,
                    status=NormalizedPaymentStatus.PENDING,
                    amount_kopecks=attempt.amount_kopecks,
                    currency=attempt.currency,
                    idempotency_key=attempt.idempotency_key,
                )
            ),
            now=expired_at,
        )
        attempt.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.EXPIRED)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertFalse(EmailDelivery.objects.filter(order=order).exists())

    def test_delivery_correction_resend_revocation_missing_object_and_restart_recovery(
        self,
    ) -> None:
        """Delivery recovery must preserve paid access while isolating each revocable bearer."""
        operator = get_user_model().objects.create_user(
            username="delivery-flow-operator", is_staff=True
        )
        order = self.make_order()
        mark_order_paid_manually(order_id=order.pk, actor=operator)
        initial = EmailDelivery.objects.get(order=order)
        correct_delivery_email(order_id=order.pk, delivery_email="corrected@example.test")
        initial.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(initial.state, EmailDelivery.State.CANCELED)
        self.assertEqual(order.checkout_email, "buyer@example.test")
        self.assertEqual(order.delivery_email, "corrected@example.test")

        resent = resend_order_access(order_id=order.pk)
        resent_grant = resent.access_grant
        revoke_order_access_grant(initial.access_grant)
        initial.access_grant.refresh_from_db()
        self.assertIsNotNone(resent_grant)
        self.assertIsNotNone(initial.access_grant.revoked_at)
        signature = sign_order_access_grant(
            grant=resent_grant,
            signing_secret="paid-photo-flow-signing-secret",
        )
        sender = DeterministicEmailSender()
        claim = claim_due_email_deliveries()[0]
        delivered = send_claimed_email_delivery(
            claim=claim,
            email_sender=sender,
            order_access_signing_secret="paid-photo-flow-signing-secret",
            order_access_url_for_grant=lambda grant, signed: (
                "https://testserver"
                + reverse(
                    "commerce:grant_order",
                    kwargs={
                        "public_number": grant.order.public_number,
                        "grant_identifier": grant.pk,
                        "signature": signed,
                    },
                )
            ),
            support_contact="support@example.test",
            timeout_seconds=20,
        )
        assert delivered is not None
        self.assertEqual(delivered.pk, resent.pk)
        self.assertEqual(sender.captured_messages[0].recipient_email, "corrected@example.test")

        missing_storage = Mock()
        missing_storage.sign_final.side_effect = ObjectMissing()
        with self.assertRaises(PurchasedOriginalUnavailable):
            sign_purchased_original(
                order=order,
                photo_id=self.photo.pk,
                purchase_browser_token=None,
                grant_identifier=resent_grant.pk,
                grant_signature=signature,
                order_access_signing_secret="paid-photo-flow-signing-secret",
                storage=missing_storage,
            )
        self.assertTrue(
            CommerceAttention.objects.filter(kind="original_missing", order=order).exists()
        )

        recovery_time = timezone.now() + timedelta(minutes=2)
        recovery_delivery = resend_order_access(order_id=order.pk, now=recovery_time)
        first_claim = claim_due_email_deliveries(now=recovery_time)[0]
        EmailDelivery.objects.filter(pk=first_claim.delivery_id).update(
            lease_expires_at=recovery_time - timedelta(seconds=1)
        )
        restarted_claim = claim_due_email_deliveries(now=recovery_time)[0]
        self.assertEqual(restarted_claim.delivery_id, recovery_delivery.pk)
        self.assertNotEqual(restarted_claim.lease_id, first_claim.lease_id)

    def test_terminal_email_failure_preserves_paid_browser_fulfillment_and_opens_attention(
        self,
    ) -> None:
        """An exhausted notification must not reverse payment, customer access, or entitlement."""
        operator = get_user_model().objects.create_user(
            username="email-flow-operator", is_staff=True
        )
        order = self.make_order()
        mark_order_paid_manually(order_id=order.pk, actor=operator)
        delivery = EmailDelivery.objects.get(order=order)
        sender = DeterministicEmailSender(outcomes=(EmailSendOutcome.TERMINAL_FAILURE,))
        claimed = claim_due_email_deliveries()[0]
        finished = send_claimed_email_delivery(
            claim=claimed,
            email_sender=sender,
            order_access_signing_secret="paid-photo-flow-signing-secret",
            order_access_url_for_grant=lambda grant, signature: (
                "https://testserver"
                + reverse(
                    "commerce:grant_order",
                    kwargs={
                        "public_number": grant.order.public_number,
                        "grant_identifier": grant.pk,
                        "signature": signature,
                    },
                )
            ),
            support_contact="support@example.test",
            timeout_seconds=20,
        )
        assert finished is not None
        order.refresh_from_db()
        self.assertEqual(finished.pk, delivery.pk)
        self.assertEqual(finished.state, EmailDelivery.State.FAILED)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(
            CommerceAttention.objects.filter(
                kind="email_exhausted", subject=f"email-delivery:{delivery.pk}", order=order
            ).exists()
        )

    def test_free_legacy_download_paid_watermark_denial_and_cart_selection_remain_separate(
        self,
    ) -> None:
        """Purchase code must not alter free downloads, paid watermark denial, or cart authority."""
        free_event = Event.objects.create(
            name="Flow free event",
            slug="paid-photo-flow-free",
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 22),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.FREE,
        )
        free_photo = Photo.objects.create(
            id="flow-free-photo",
            event=free_event,
            src="",
            original_key="private/flow-free-photo.jpg",
            original_filename="flow-free-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=self.photographer,
        )
        resolver = Mock()
        resolver.resolve_download.return_value = "https://storage.test.invalid/free-original"
        with patch(
            "config.views.gallery_photo_queryset",
            return_value=Photo.objects.filter(pk=free_photo.pk),
        ):
            with patch("config.views._public_media_resolver", return_value=resolver):
                free_download = self.client.get(
                    reverse(
                        "photo_download",
                        kwargs={"slug": free_event.slug, "photo_id": free_photo.pk},
                    )
                )
        self.assertEqual(free_download.status_code, 302)
        self.assertEqual(free_download["Location"], "https://storage.test.invalid/free-original")

        legacy_photo = Photo.objects.create(
            id="flow-legacy-photo",
            event=self.event,
            src="",
            original_key="private/flow-legacy-photo.jpg",
            original_filename="flow-legacy-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=self.photographer,
        )
        legacy_token = "paid-photo-flow-legacy-result"
        legacy_search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=hashlib.sha256(legacy_token.encode()).hexdigest(),
            status=SelfieSearch.Status.READY,
            temporary_object_key="",
            configuration={"public-contract": 1},
            eligible_photo_count=1,
            matched_photo_count=1,
        )
        SelfieSearchResult.objects.create(search=legacy_search, photo=legacy_photo, rank=1)
        self.feature_flag_states["paid-watermarked-previews"] = FEATURE_FLAG_STAFF
        legacy_staff = get_user_model().objects.create_user(
            username="legacy-flow-staff", is_staff=True
        )
        self.client.force_login(legacy_staff)
        legacy_page = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": legacy_token},
            )
        )
        self.assertEqual(legacy_page.status_code, 200)
        self.assertContains(legacy_page, "gallery-lightbox-download")

        self.client.logout()
        paid_resolver = Mock()
        with patch(
            "config.views.gallery_photo_queryset",
            return_value=Photo.objects.filter(pk=self.photo.pk),
        ):
            with patch("config.views._public_media_resolver", return_value=paid_resolver):
                public_paid_download = self.client.get(
                    reverse(
                        "photo_download",
                        kwargs={"slug": self.event.slug, "photo_id": self.photo.pk},
                    )
                )
        self.assertEqual(public_paid_download.status_code, 404)
        paid_resolver.resolve_download.assert_not_called()

        cleared = clear_cart(event=self.event, browser_token=self.cart_token)
        self.assertTrue(cleared.changed)
        self.assertEqual(cleared.snapshot.item_count, 0)
        self.assertFalse(Cart.objects.filter(event=self.event).exists())
