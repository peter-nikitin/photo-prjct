import base64
import importlib
import importlib.util
from datetime import UTC, date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from picflow.models import Event, Photo

from commerce import models as commerce_models
from commerce.models import Order, OrderItem


def load_capabilities():
    assert importlib.util.find_spec("commerce.capabilities") is not None, (
        "commerce.capabilities must implement the fulfillment capability contract"
    )
    return importlib.import_module("commerce.capabilities")


def require_model(name: str):
    assert hasattr(commerce_models, name), f"commerce.models.{name} must exist"
    return getattr(commerce_models, name)


class CapabilityTests(TestCase):
    """The breaks caught here would expose or broaden a customer bearer capability."""

    def setUp(self) -> None:
        self.capabilities = load_capabilities()
        self.OrderAccessGrant = require_model("OrderAccessGrant")
        self.event = Event.objects.create(
            name="Capability event",
            slug="capability-event",
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="capability-photo",
            event=self.event,
            src="photos/capability.jpg",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SET CONSTRAINTS commerce_order_insert_total_guard, "
                "commerce_order_item_total_guard DEFERRED"
            )

    def make_order(self, *, public_number: str, browser_digest: str) -> Order:
        order = Order.objects.create(
            public_number=public_number,
            event=self.event,
            checkout_email="buyer@example.test",
            total_kopecks=30000,
            purchase_browser_token_sha256=browser_digest,
        )
        OrderItem.objects.create(
            order=order,
            photo=self.photo,
            photo_public_id=self.photo.pk,
            unit_price_kopecks=30000,
            quantity=1,
            line_total_kopecks=30000,
        )
        return order

    def test_purchase_browser_issue_uses_32_random_bytes_and_refreshes_only_for_order_creation(
        self,
    ) -> None:
        """A short token or read-time expiry refresh would weaken or prolong browser authority."""
        created_at = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)

        issued = self.capabilities.issue_purchase_browser_capability(order_created_at=created_at)
        decoded = base64.urlsafe_b64decode(issued.token + "=")

        self.assertEqual(len(decoded), 32)
        self.assertEqual(issued.expires_at, created_at + timedelta(days=30))
        self.assertNotIn(issued.token, repr(issued))

        later_order_at = created_at + timedelta(days=4)
        refreshed = self.capabilities.issue_purchase_browser_capability(
            order_created_at=later_order_at,
            existing_token=issued.token,
        )
        self.assertEqual(refreshed.token, issued.token)
        self.assertEqual(refreshed.token_sha256, issued.token_sha256)
        self.assertEqual(refreshed.expires_at, later_order_at + timedelta(days=30))

        order = self.make_order(
            public_number="FM-CAPAB234",
            browser_digest=issued.token_sha256,
        )
        original_expiry = issued.expires_at
        self.assertTrue(
            self.capabilities.purchase_browser_authorizes_order(
                order=order,
                token=issued.token,
            )
        )
        self.assertEqual(issued.expires_at, original_expiry)

    def test_purchase_browser_verification_is_digest_only_and_sanitizes_invalid_or_foreign_tokens(
        self,
    ) -> None:
        """A raw-token column or distinguishable invalid input could leak browser authority."""
        first = self.capabilities.issue_purchase_browser_capability(
            order_created_at=datetime(2026, 8, 21, tzinfo=UTC)
        )
        second = self.capabilities.issue_purchase_browser_capability(
            order_created_at=datetime(2026, 8, 21, tzinfo=UTC)
        )
        first_order = self.make_order(
            public_number="FM-DJGSTA23",
            browser_digest=first.token_sha256,
        )
        foreign_order = self.make_order(
            public_number="FM-FREJGN23",
            browser_digest=second.token_sha256,
        )

        self.assertTrue(
            self.capabilities.purchase_browser_authorizes_order(
                order=first_order,
                token=first.token,
            )
        )
        for order, token in (
            (foreign_order, first.token),
            (first_order, second.token),
            (first_order, ""),
            (first_order, "not-a-capability"),
            (first_order, None),
        ):
            with self.subTest(order=order.public_number, token_type=type(token).__name__):
                self.assertFalse(
                    self.capabilities.purchase_browser_authorizes_order(
                        order=order,
                        token=token,
                    )
                )

        order_fields = {field.name for field in Order._meta.local_fields}
        self.assertIn("purchase_browser_token_sha256", order_fields)
        self.assertTrue(
            {
                "purchase_browser_token",
                "raw_token",
                "browser_cookie",
                "access_url",
                "signed_url",
            }.isdisjoint(order_fields)
        )

    def test_purchase_browser_expiry_uses_latest_order_creation_without_read_refresh(self) -> None:
        """An expired cookie or a read-time refresh would prolong browser authority."""
        issued = self.capabilities.issue_purchase_browser_capability()
        first_order = self.make_order(
            public_number="FM-EXPJRE23",
            browser_digest=issued.token_sha256,
        )
        first_expiry = first_order.created_at + timedelta(days=30)
        original_first_values = (
            Order.objects.filter(pk=first_order.pk)
            .values(
                "created_at",
                "first_customer_access_at",
            )
            .get()
        )

        def authorizes(*, order: Order, now: datetime) -> bool:
            try:
                return self.capabilities.purchase_browser_authorizes_order(
                    order=order,
                    token=issued.token,
                    now=now,
                )
            except TypeError as error:
                self.fail(f"purchase-browser verification must accept the check time: {error}")

        self.assertTrue(authorizes(order=first_order, now=first_expiry - timedelta(microseconds=1)))
        self.assertFalse(authorizes(order=first_order, now=first_expiry))
        self.assertFalse(authorizes(order=first_order, now=first_expiry + timedelta(days=1)))

        second_order = self.make_order(
            public_number="FM-EXPJRE24",
            browser_digest=issued.token_sha256,
        )
        second_expiry = second_order.created_at + timedelta(days=30)
        self.assertTrue(authorizes(order=first_order, now=first_expiry))
        self.assertFalse(authorizes(order=first_order, now=second_expiry))
        self.assertEqual(
            Order.objects.filter(pk=first_order.pk)
            .values(
                "created_at",
                "first_customer_access_at",
            )
            .get(),
            original_first_values,
        )

    @override_settings(SECRET_KEY="must-not-sign-order-access")
    def test_order_grant_signature_is_stable_hmac_from_only_the_dedicated_argument(self) -> None:
        """Using SECRET_KEY or transient state would silently invalidate permanent links."""
        issue = self.capabilities.issue_purchase_browser_capability(
            order_created_at=datetime(2026, 8, 21, tzinfo=UTC)
        )
        order = self.make_order(
            public_number="FM-HMACSGN2",
            browser_digest=issue.token_sha256,
        )
        grant = self.capabilities.create_order_access_grant(
            order=order,
            source=self.OrderAccessGrant.Source.CHECKOUT,
        )

        signature = self.capabilities.sign_order_access_grant(
            grant=grant,
            signing_secret="dedicated-stable-signing-key",
        )
        grant.refresh_from_db()
        repeated_signature = self.capabilities.sign_order_access_grant(
            grant=grant,
            signing_secret="dedicated-stable-signing-key",
        )

        self.assertEqual(repeated_signature, signature)
        self.assertNotEqual(
            self.capabilities.sign_order_access_grant(
                grant=grant,
                signing_secret="different-dedicated-key",
            ),
            signature,
        )
        self.assertNotIn(signature, str(grant))
        self.assertNotIn(signature, repr(grant))

    def test_multiple_grants_coexist_and_revocation_affects_only_one(self) -> None:
        """Creating or revoking one link must not revoke another permanent customer link."""
        issue = self.capabilities.issue_purchase_browser_capability(
            order_created_at=datetime(2026, 8, 21, tzinfo=UTC)
        )
        order = self.make_order(
            public_number="FM-MULTJ234",
            browser_digest=issue.token_sha256,
        )
        first = self.capabilities.create_order_access_grant(
            order=order,
            source=self.OrderAccessGrant.Source.CHECKOUT,
        )
        second = self.capabilities.create_order_access_grant(
            order=order,
            source=self.OrderAccessGrant.Source.RESEND,
        )
        secret = "dedicated-stable-signing-key"
        first_signature = self.capabilities.sign_order_access_grant(
            grant=first,
            signing_secret=secret,
        )
        second_signature = self.capabilities.sign_order_access_grant(
            grant=second,
            signing_secret=secret,
        )

        self.assertEqual(
            self.capabilities.verify_order_access_grant(
                order=order,
                grant_identifier=str(first.pk),
                signature=first_signature,
                signing_secret=secret,
            ),
            first,
        )
        self.assertEqual(
            self.capabilities.verify_order_access_grant(
                order=order,
                grant_identifier=str(second.pk),
                signature=second_signature,
                signing_secret=secret,
            ),
            second,
        )

        revoked_at = datetime(2026, 8, 21, 12, tzinfo=UTC)
        self.capabilities.revoke_order_access_grant(first, revoked_at=revoked_at)
        self.assertIsNone(
            self.capabilities.verify_order_access_grant(
                order=order,
                grant_identifier=str(first.pk),
                signature=first_signature,
                signing_secret=secret,
            )
        )
        self.assertEqual(
            self.capabilities.verify_order_access_grant(
                order=order,
                grant_identifier=str(second.pk),
                signature=second_signature,
                signing_secret=secret,
            ),
            second,
        )

    def test_order_grant_verification_returns_one_sanitized_denial_for_invalid_or_foreign_input(
        self,
    ) -> None:
        """Malformed, forged, and cross-order links must all fail without broadened access."""
        first_issue = self.capabilities.issue_purchase_browser_capability()
        second_issue = self.capabilities.issue_purchase_browser_capability()
        first_order = self.make_order(
            public_number="FM-GRANT234",
            browser_digest=first_issue.token_sha256,
        )
        foreign_order = self.make_order(
            public_number="FM-GRANT235",
            browser_digest=second_issue.token_sha256,
        )
        grant = self.capabilities.create_order_access_grant(
            order=first_order,
            source=self.OrderAccessGrant.Source.CHECKOUT,
        )
        secret = "dedicated-stable-signing-key"
        signature = self.capabilities.sign_order_access_grant(
            grant=grant,
            signing_secret=secret,
        )

        for order, identifier, presented_signature in (
            (foreign_order, str(grant.pk), signature),
            (first_order, "not-a-uuid", signature),
            (first_order, str(grant.pk), "forged"),
            (first_order, str(grant.pk), ""),
            (first_order, str(grant.pk), None),
        ):
            with self.subTest(identifier=identifier, order=order.public_number):
                self.assertIsNone(
                    self.capabilities.verify_order_access_grant(
                        order=order,
                        grant_identifier=identifier,
                        signature=presented_signature,
                        signing_secret=secret,
                    )
                )

    def test_grant_storage_contains_metadata_but_no_complete_bearer(self) -> None:
        """Persisting a complete bearer would turn a database read into customer access."""
        fields = {field.name for field in self.OrderAccessGrant._meta.local_fields}

        self.assertTrue(
            {"id", "order", "source", "created_by", "created_at", "revoked_at"}.issubset(fields)
        )
        self.assertTrue(
            {
                "signature",
                "signing_secret",
                "raw_token",
                "bearer_url",
                "access_url",
                "signed_url",
            }.isdisjoint(fields)
        )

    def test_admin_grant_helper_requires_and_persists_the_creation_actor(self) -> None:
        """An administrator link without an actor would create unattributed authority."""
        issued = self.capabilities.issue_purchase_browser_capability()
        order = self.make_order(
            public_number="FM-ADMJN234",
            browser_digest=issued.token_sha256,
        )

        with self.assertRaisesRegex(ValueError, "actor"):
            self.capabilities.create_order_access_grant(
                order=order,
                source=self.OrderAccessGrant.Source.ADMIN,
            )

        actor = get_user_model().objects.create_user(username="grant-admin")
        grant = self.capabilities.create_order_access_grant(
            order=order,
            source=self.OrderAccessGrant.Source.ADMIN,
            created_by=actor,
        )
        self.assertEqual(grant.created_by, actor)

    def test_admin_grant_database_rejects_a_missing_creation_actor(self) -> None:
        """Direct ORM insertion must not bypass administrator grant attribution."""
        issued = self.capabilities.issue_purchase_browser_capability()
        order = self.make_order(
            public_number="FM-ADMJN235",
            browser_digest=issued.token_sha256,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.OrderAccessGrant.objects.create(
                order=order,
                source=self.OrderAccessGrant.Source.ADMIN,
            )
