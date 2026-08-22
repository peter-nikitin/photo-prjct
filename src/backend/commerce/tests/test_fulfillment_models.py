from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from commerce import models as commerce_models
from commerce.models import Order, OrderItem, PaymentAttempt


def require_model(name: str):
    assert hasattr(commerce_models, name), f"commerce.models.{name} must exist"
    return getattr(commerce_models, name)


class FulfillmentModelTests(TestCase):
    """The breaks caught here would rewrite fulfillment history or retain a customer bearer."""

    def setUp(self) -> None:
        self.OrderAccessGrant = require_model("OrderAccessGrant")
        self.EmailDelivery = require_model("EmailDelivery")
        self.EmailDeliveryAttempt = require_model("EmailDeliveryAttempt")
        self.CommerceAttention = require_model("CommerceAttention")
        self.DownloadGrantAudit = require_model("DownloadGrantAudit")
        self.event = Event.objects.create(
            name="Fulfillment event",
            slug="fulfillment-event",
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="fulfillment-photo",
            event=self.event,
            src="photos/fulfillment.jpg",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SET CONSTRAINTS commerce_order_insert_total_guard, "
                "commerce_order_item_total_guard DEFERRED"
            )
        self.order = Order.objects.create(
            public_number="FM-FULFJLL2",
            event=self.event,
            checkout_email="buyer@example.test",
            total_kopecks=30000,
            purchase_browser_token_sha256="a" * 64,
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            photo=self.photo,
            photo_public_id=self.photo.pk,
            unit_price_kopecks=30000,
            quantity=1,
            line_total_kopecks=30000,
        )
        self.grant = self.OrderAccessGrant.objects.create(
            order=self.order,
            source=self.OrderAccessGrant.Source.CHECKOUT,
        )

    def make_delivery(self, *, recipient: str = "buyer@example.test"):
        return self.EmailDelivery.objects.create(
            order=self.order,
            message_kind=self.EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=recipient,
            access_grant=self.grant,
            next_attempt_at=timezone.now(),
        )

    def make_foreign_grant(self):
        foreign_order = Order.objects.create(
            public_number="FM-FREJGN24",
            event=self.event,
            checkout_email="other@example.test",
            total_kopecks=30000,
            purchase_browser_token_sha256="b" * 64,
        )
        OrderItem.objects.create(
            order=foreign_order,
            photo=self.photo,
            photo_public_id=self.photo.pk,
            unit_price_kopecks=30000,
            quantity=1,
            line_total_kopecks=30000,
        )
        return self.OrderAccessGrant.objects.create(
            order=foreign_order,
            source=self.OrderAccessGrant.Source.CHECKOUT,
        )

    def test_order_keeps_only_an_immutable_browser_digest_and_sets_first_access_once(self) -> None:
        """Rewriting browser identity or first access would falsify authorization history."""
        digest_field = Order._meta.get_field("purchase_browser_token_sha256")
        digest_field.run_validators("a" * 64)
        for invalid in ("A" * 64, "a" * 63, "g" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    digest_field.run_validators(invalid)

        self.order.purchase_browser_token_sha256 = "b" * 64
        with self.assertRaisesRegex(ValidationError, "immutable"):
            self.order.save(update_fields=["purchase_browser_token_sha256"])
        self.order.refresh_from_db()

        first_access = timezone.now()
        self.order.first_customer_access_at = first_access
        self.order.save(update_fields=["first_customer_access_at"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.first_customer_access_at, first_access)

        with self.assertRaisesRegex(ValidationError, "set once"):
            self.order.first_customer_access_at = first_access + timedelta(seconds=1)
            self.order.save(update_fields=["first_customer_access_at"])
        self.order.refresh_from_db()
        with self.assertRaises(DatabaseError), transaction.atomic():
            Order.objects.filter(pk=self.order.pk).update(
                first_customer_access_at=first_access + timedelta(seconds=2)
            )

    def test_delivery_snapshots_recipient_and_has_only_the_approved_states(self) -> None:
        """Editing a queued recipient or inventing a state would corrupt delivery evidence."""
        delivery = self.make_delivery()

        self.assertEqual(
            {value for value, _label in self.EmailDelivery.State.choices},
            {"pending", "processing", "retry_wait", "succeeded", "failed", "canceled"},
        )
        self.order.delivery_email = "corrected@example.test"
        self.order.save(update_fields=["delivery_email"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.recipient_email, "buyer@example.test")

        delivery.recipient_email = "rewritten@example.test"
        with self.assertRaisesRegex(ValidationError, "snapshot is immutable"):
            delivery.save(update_fields=["recipient_email"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.EmailDelivery.objects.create(
                order=self.order,
                message_kind=self.EmailDelivery.MessageKind.ORDER_ACCESS,
                recipient_email="buyer@example.test",
                access_grant=self.grant,
                state="invented",
                next_attempt_at=timezone.now(),
            )

    def test_delivery_processing_requires_a_bounded_lease(self) -> None:
        """Processing without an expiring claim could leave email work stuck forever."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.EmailDelivery.objects.create(
                order=self.order,
                message_kind=self.EmailDelivery.MessageKind.ORDER_ACCESS,
                recipient_email="buyer@example.test",
                access_grant=self.grant,
                state=self.EmailDelivery.State.PROCESSING,
                next_attempt_at=timezone.now(),
            )

        delivery = self.make_delivery()
        delivery.state = self.EmailDelivery.State.PROCESSING
        delivery.lease_id = "12345678-1234-5678-1234-567812345678"
        delivery.lease_expires_at = timezone.now() + timedelta(minutes=5)
        delivery.save(update_fields=["state", "lease_id", "lease_expires_at"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, self.EmailDelivery.State.PROCESSING)

    def test_delivery_rejects_an_access_grant_from_another_order(self) -> None:
        """A foreign grant would email access to an Order other than the delivery snapshot."""
        foreign_grant = self.make_foreign_grant()

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.EmailDelivery.objects.create(
                order=self.order,
                message_kind=self.EmailDelivery.MessageKind.ORDER_ACCESS,
                recipient_email="buyer@example.test",
                access_grant=foreign_grant,
                next_attempt_at=timezone.now(),
            )

    def test_delivery_insert_requires_the_current_order_recipient(self) -> None:
        """A stale or unrelated recipient would send access outside the approved snapshot."""
        self.order.delivery_email = "corrected@example.test"
        self.order.save(update_fields=["delivery_email"])

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.make_delivery(recipient="buyer@example.test")

        delivery = self.make_delivery(recipient="corrected@example.test")
        self.assertEqual(delivery.recipient_email, self.order.delivery_email)

    def test_delivery_attempts_are_immutable_recipient_snapshots(self) -> None:
        """Rewriting or deleting an attempt would hide where and why delivery occurred."""
        delivery = self.make_delivery()
        attempt = self.EmailDeliveryAttempt.objects.create(
            delivery=delivery,
            attempt_number=1,
            recipient_email=delivery.recipient_email,
            outcome=self.EmailDeliveryAttempt.Outcome.RETRYABLE_FAILURE,
            safe_failure_category="provider_timeout",
            attempted_at=timezone.now(),
        )

        self.assertEqual(attempt.recipient_email, "buyer@example.test")
        attempt.safe_failure_category = "rewritten"
        with self.assertRaisesRegex(ValidationError, "append-only"):
            attempt.save(update_fields=["safe_failure_category"])
        with self.assertRaisesRegex(ValidationError, "append-only"):
            attempt.delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.EmailDeliveryAttempt.objects.filter(pk=attempt.pk).update(
                outcome=self.EmailDeliveryAttempt.Outcome.SUCCEEDED
            )

    def test_delivery_attempt_insert_requires_the_delivery_recipient_snapshot(self) -> None:
        """An attempt for another recipient would falsify where the email was sent."""
        delivery = self.make_delivery()

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.EmailDeliveryAttempt.objects.create(
                delivery=delivery,
                attempt_number=1,
                recipient_email="other@example.test",
                outcome=self.EmailDeliveryAttempt.Outcome.RETRYABLE_FAILURE,
                safe_failure_category="provider_timeout",
                attempted_at=timezone.now(),
            )

    def test_attention_identity_deduplicates_kind_and_subject_and_keeps_resolution(self) -> None:
        """Duplicate rows would spam operators and lose recovery resolution facts."""
        observed_at = timezone.now()
        attention = self.CommerceAttention.objects.create(
            kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
            subject=f"order-item:{self.item.pk}",
            order=self.order,
            first_observed_at=observed_at,
            last_observed_at=observed_at,
            next_reminder_at=observed_at,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.CommerceAttention.objects.create(
                kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
                subject=f"order-item:{self.item.pk}",
                order=self.order,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                next_reminder_at=observed_at,
            )

        resolved_at = observed_at + timedelta(minutes=5)
        attention.last_observed_at = observed_at + timedelta(minutes=1)
        attention.resolved_at = resolved_at
        attention.resolution_source = self.CommerceAttention.ResolutionSource.ADMIN
        attention.resolution_comment = "Original restored and verified."
        attention.save(
            update_fields=[
                "last_observed_at",
                "resolved_at",
                "resolution_source",
                "resolution_comment",
            ]
        )
        attention.refresh_from_db()
        self.assertEqual(attention.resolved_at, resolved_at)
        self.assertEqual(attention.resolution_source, "admin")

    def test_resolved_attention_allows_one_new_open_recurrence(self) -> None:
        """Lifetime uniqueness would suppress a real recurrence after operator resolution."""
        observed_at = timezone.now()
        subject = f"order-item:{self.item.pk}"
        first = self.CommerceAttention.objects.create(
            kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
            subject=subject,
            order=self.order,
            first_observed_at=observed_at,
            last_observed_at=observed_at,
        )
        first.resolved_at = observed_at + timedelta(minutes=1)
        first.resolution_source = self.CommerceAttention.ResolutionSource.AUTOMATIC
        first.save(update_fields=["resolved_at", "resolution_source"])

        try:
            recurring = self.CommerceAttention.objects.create(
                kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
                subject=subject,
                order=self.order,
                first_observed_at=observed_at + timedelta(minutes=2),
                last_observed_at=observed_at + timedelta(minutes=2),
            )
        except IntegrityError as error:
            self.fail(f"resolved attention must allow one new open recurrence: {error}")
        self.assertEqual(
            self.CommerceAttention.objects.filter(
                kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
                subject=subject,
            ).count(),
            2,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.CommerceAttention.objects.create(
                kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
                subject=subject,
                order=self.order,
                first_observed_at=observed_at + timedelta(minutes=3),
                last_observed_at=observed_at + timedelta(minutes=3),
            )
        self.assertIsNone(recurring.resolved_at)

    def test_attention_identity_references_and_first_observation_are_immutable(self) -> None:
        """Rewriting problem identity or safe references would detach operator evidence."""
        observed_at = timezone.now()
        attempt = PaymentAttempt.objects.create(
            order=self.order,
            amount_kopecks=self.order.total_kopecks,
            currency=self.order.currency,
            adapter_key="test-gateway",
            idempotency_key="attention-identity-attempt",
        )
        attention = self.CommerceAttention.objects.create(
            kind=self.CommerceAttention.Kind.PAYMENT_MISMATCH,
            subject=f"payment-attempt:{attempt.pk}",
            order=self.order,
            payment_attempt=attempt,
            first_observed_at=observed_at,
            last_observed_at=observed_at,
        )

        for field, replacement in (
            ("kind", self.CommerceAttention.Kind.ORIGINAL_MISSING),
            ("subject", "rewritten-subject"),
            ("order_id", None),
            ("payment_attempt_id", None),
            ("first_observed_at", observed_at - timedelta(days=1)),
        ):
            with self.subTest(field=field):
                setattr(attention, field, replacement)
                with self.assertRaisesRegex(ValidationError, "identity is immutable"):
                    attention.save()
                attention.refresh_from_db()

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.CommerceAttention.objects.filter(pk=attention.pk).update(
                subject="bulk-rewritten-subject"
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE commerce_commerceattention SET first_observed_at = %s WHERE id = %s",
                    [observed_at - timedelta(days=2), attention.pk],
                )

    def test_completed_attention_resolution_is_write_once(self) -> None:
        """Clearing or rewriting resolution would falsify completed operator recovery."""
        observed_at = timezone.now()
        attention = self.CommerceAttention.objects.create(
            kind=self.CommerceAttention.Kind.ORIGINAL_MISSING,
            subject=f"order-item:{self.item.pk}",
            order=self.order,
            first_observed_at=observed_at,
            last_observed_at=observed_at,
        )
        attention.resolved_at = observed_at + timedelta(minutes=1)
        attention.resolution_source = self.CommerceAttention.ResolutionSource.ADMIN
        attention.resolution_comment = "Original restored."
        attention.save(update_fields=["resolved_at", "resolution_source", "resolution_comment"])

        for field, replacement in (
            ("resolved_at", None),
            ("resolution_source", self.CommerceAttention.ResolutionSource.AUTOMATIC),
            ("resolution_comment", "Rewritten resolution."),
        ):
            with self.subTest(field=field):
                setattr(attention, field, replacement)
                with self.assertRaisesRegex(ValidationError, "resolution is write-once"):
                    attention.save()
                attention.refresh_from_db()

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.CommerceAttention.objects.filter(pk=attention.pk).update(
                resolved_at=None,
                resolution_source="",
                resolution_comment="",
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE commerce_commerceattention SET resolution_comment = %s WHERE id = %s",
                    ["Direct rewrite.", attention.pk],
                )

    def test_attention_covers_each_initial_operator_problem_with_safe_references(self) -> None:
        """Omitting a kind would force failures into logs or personal data."""
        self.assertEqual(
            {value for value, _label in self.CommerceAttention.Kind.choices},
            {
                "payment_mismatch",
                "manual_payment_conflict",
                "original_missing",
                "email_exhausted",
                "payment_reconciliation_overdue",
                "commerce_work_stale",
            },
        )
        fields = {field.name for field in self.CommerceAttention._meta.local_fields}
        self.assertTrue({"kind", "subject", "order", "payment_attempt"}.issubset(fields))
        self.assertTrue(
            {
                "customer_email",
                "raw_callback",
                "provider_secret",
                "access_url",
                "signature",
            }.isdisjoint(fields)
        )

    def test_download_audit_records_issuance_source_without_network_or_bearer_data(self) -> None:
        """Audit must prove issuance without retaining customer network identity or access links."""
        browser_audit = self.DownloadGrantAudit.objects.create(
            order_item=self.item,
            authorization_source=self.DownloadGrantAudit.AuthorizationSource.PURCHASE_BROWSER,
        )
        grant_audit = self.DownloadGrantAudit.objects.create(
            order_item=self.item,
            authorization_source=self.DownloadGrantAudit.AuthorizationSource.ORDER_ACCESS_GRANT,
            access_grant=self.grant,
        )

        self.assertIsNotNone(browser_audit.created_at)
        self.assertEqual(grant_audit.access_grant, self.grant)
        fields = {field.name for field in self.DownloadGrantAudit._meta.local_fields}
        self.assertTrue(
            {"order_item", "authorization_source", "access_grant", "created_at"}.issubset(fields)
        )
        self.assertTrue(
            {
                "access_url",
                "signature",
                "signed_url",
                "raw_token",
                "ip_address",
                "user_agent",
                "request_headers",
            }.isdisjoint(fields)
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.DownloadGrantAudit.objects.create(
                order_item=self.item,
                authorization_source=(
                    self.DownloadGrantAudit.AuthorizationSource.ORDER_ACCESS_GRANT
                ),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.DownloadGrantAudit.objects.create(
                order_item=self.item,
                authorization_source=self.DownloadGrantAudit.AuthorizationSource.PURCHASE_BROWSER,
                access_grant=self.grant,
            )

    def test_download_audit_is_append_only(self) -> None:
        """Editing or deleting an issuance record would falsify customer-access history."""
        audit = self.DownloadGrantAudit.objects.create(
            order_item=self.item,
            authorization_source=self.DownloadGrantAudit.AuthorizationSource.PURCHASE_BROWSER,
        )

        audit.authorization_source = self.DownloadGrantAudit.AuthorizationSource.ORDER_ACCESS_GRANT
        audit.access_grant = self.grant
        with self.assertRaisesRegex(ValidationError, "append-only"):
            audit.save(update_fields=["authorization_source", "access_grant"])
        with self.assertRaisesRegex(ValidationError, "append-only"):
            audit.delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.DownloadGrantAudit.objects.filter(pk=audit.pk).delete()

    def test_named_download_audit_rejects_a_grant_from_another_order(self) -> None:
        """A cross-order audit would claim the wrong named grant authorized an original."""
        foreign_grant = self.make_foreign_grant()

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.DownloadGrantAudit.objects.create(
                order_item=self.item,
                authorization_source=(
                    self.DownloadGrantAudit.AuthorizationSource.ORDER_ACCESS_GRANT
                ),
                access_grant=foreign_grant,
            )

    def test_fulfillment_tables_store_no_complete_customer_bearer_or_rendered_message(self) -> None:
        """A database export must not contain a reusable permanent customer URL or raw secret."""
        models = (
            self.OrderAccessGrant,
            self.EmailDelivery,
            self.EmailDeliveryAttempt,
            self.CommerceAttention,
            self.DownloadGrantAudit,
        )
        stored_fields = {field.name for model in models for field in model._meta.local_fields}

        self.assertTrue(
            {
                "bearer_url",
                "access_url",
                "signature",
                "signing_secret",
                "raw_token",
                "signed_url",
                "rendered_body",
                "message_body",
                "request_headers",
            }.isdisjoint(stored_fields)
        )
