import importlib
import importlib.util
import inspect
from datetime import UTC, date, datetime, timedelta
from typing import Never

from django.contrib.auth import get_user_model
from django.test import TestCase
from ingestion.storage import ObjectMismatch, ObjectMissing, StorageUnavailable
from picflow.models import Event, Photo

from commerce.capabilities import (
    create_order_access_grant,
    issue_purchase_browser_capability,
    revoke_order_access_grant,
    sign_order_access_grant,
)
from commerce.identity import browser_token_sha256
from commerce.models import CommerceAttention, DownloadGrantAudit, Order, OrderItem


def load_original_delivery():
    assert importlib.util.find_spec("commerce.original_delivery") is not None, (
        "commerce.original_delivery must authorize and sign purchased originals"
    )
    return importlib.import_module("commerce.original_delivery")


class _RecordingStorage:
    def __init__(
        self, response: str | Exception = "https://storage.example.test/purchased"
    ) -> None:
        self.response = response
        self.signed_requests: list[tuple[str, str | None]] = []

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:
        self.signed_requests.append((key, attachment_filename))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def open_final(self, *, key: str) -> Never:  # noqa: ARG002
        raise AssertionError("Purchased delivery must not read or substitute public media.")


class OriginalDeliveryTests(TestCase):
    """The breaks caught here would expose an original outside one paid OrderItem."""

    purchase_token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    cart_token = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
    signing_secret = "dedicated-order-access-signing-secret"

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        self.photographer = get_user_model().objects.create_user(username="delivery-photographer")
        self.event = Event.objects.create(
            name="Original delivery event",
            slug="original-delivery-event",
            start_date=date(2026, 8, 21),
            end_date=date(2026, 8, 21),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.other_event = Event.objects.create(
            name="Other original delivery event",
            slug="other-original-delivery-event",
            start_date=date(2026, 8, 21),
            end_date=date(2026, 8, 21),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = self.make_private_photo(
            event=self.event,
            photo_id="paid-photo",
            original_key="originals/0123456789abcdef0123456789abcdef",
        )
        self.unpurchased_photo = self.make_private_photo(
            event=self.event,
            photo_id="not-purchased-photo",
            original_key="originals/11111111111111111111111111111111",
        )
        self.other_event_photo = self.make_private_photo(
            event=self.other_event,
            photo_id="other-event-photo",
            original_key="originals/22222222222222222222222222222222",
        )
        self.purchase_capability = issue_purchase_browser_capability(order_created_at=self.now)
        self.order = self.make_order(
            public_number="FM-ABCD2345",
            event=self.event,
            photo=self.photo,
            browser_digest=self.purchase_capability.token_sha256,
            status=Order.Status.PAID,
        )
        self.grant = create_order_access_grant(
            order=self.order,
            source="checkout",
        )

    def make_private_photo(
        self,
        *,
        event: Event,
        photo_id: str,
        original_key: str,
        original_content_type: str = "image/jpeg",
    ) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=event,
            src="",
            uploaded_by=self.photographer,
            original_key=original_key,
            original_filename="camera-name.jpg",
            original_size=123,
            original_content_type=original_content_type,
            uploaded_at=self.now,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

    def make_order(
        self,
        *,
        public_number: str,
        event: Event,
        photo: Photo,
        browser_digest: str,
        status: object,
    ) -> Order:
        normalized_status = str(status)
        order = Order.objects.create(
            public_number=public_number,
            event=event,
            purchase_browser_token_sha256=browser_digest,
            checkout_email="buyer@example.test",
            total_kopecks=30000,
            status=normalized_status,
            paid_at=self.now if normalized_status == str(Order.Status.PAID) else None,
        )
        OrderItem.objects.create(
            order=order,
            photo=photo,
            photo_public_id=photo.pk,
            unit_price_kopecks=30000,
            line_total_kopecks=30000,
        )
        return order

    def issue_with_browser(self, delivery, storage: _RecordingStorage, **overrides):
        return delivery.sign_purchased_original(
            order=overrides.pop("order", self.order),
            photo_id=overrides.pop("photo_id", self.photo.pk),
            purchase_browser_token=overrides.pop(
                "purchase_browser_token", self.purchase_capability.token
            ),
            grant_identifier=overrides.pop("grant_identifier", None),
            grant_signature=overrides.pop("grant_signature", None),
            order_access_signing_secret=overrides.pop(
                "order_access_signing_secret", self.signing_secret
            ),
            storage=storage,
            now=overrides.pop("now", self.now),
            **overrides,
        )

    def issue_with_grant(self, delivery, storage: _RecordingStorage, **overrides):
        grant = overrides.pop("grant", self.grant)
        signature = sign_order_access_grant(
            grant=grant,
            signing_secret=self.signing_secret,
        )
        return self.issue_with_browser(
            delivery,
            storage,
            purchase_browser_token=None,
            grant_identifier=str(grant.pk),
            grant_signature=signature,
            **overrides,
        )

    def test_paid_browser_capability_signs_the_exact_jpeg_and_appends_browser_audit(self) -> None:
        """Dropping paid-item or browser checks would expose an original to a cart holder."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()

        download = self.issue_with_browser(delivery, storage)

        self.assertEqual(download.signed_url, "https://storage.example.test/purchased")
        self.assertNotIn(download.signed_url, repr(download))
        self.assertEqual(
            storage.signed_requests,
            [(self.photo.original_key, "findme-photo-paid-photo.jpg")],
        )
        audit = DownloadGrantAudit.objects.get()
        self.assertEqual(audit.order_item.order_id, self.order.pk)
        self.assertEqual(
            audit.authorization_source,
            DownloadGrantAudit.AuthorizationSource.PURCHASE_BROWSER,
        )
        self.assertIsNone(audit.access_grant)
        self.order.refresh_from_db()
        self.assertEqual(self.order.first_customer_access_at, self.now)
        self.assertNotIn(
            "original_key", inspect.signature(delivery.sign_purchased_original).parameters
        )

    def test_active_grant_is_permanent_and_each_successful_reissue_has_a_named_audit(self) -> None:
        """Expiry, single-use links, or anonymous audits would break permanent recovery access."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()

        first = self.issue_with_grant(delivery, storage)
        second = self.issue_with_grant(delivery, storage, now=self.now + timedelta(days=365))

        self.assertEqual(first.signed_url, second.signed_url)
        self.assertEqual(len(storage.signed_requests), 2)
        audits = list(DownloadGrantAudit.objects.order_by("pk"))
        self.assertEqual(len(audits), 2)
        self.assertEqual(
            [audit.authorization_source for audit in audits],
            [DownloadGrantAudit.AuthorizationSource.ORDER_ACCESS_GRANT] * 2,
        )
        self.assertEqual(
            [audit.access_grant_id for audit in audits], [self.grant.pk, self.grant.pk]
        )

    def test_foreign_order_or_photo_never_reaches_signing(self) -> None:
        """Using a valid capability or item for a different Order must not broaden delivery."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()
        foreign_capability = issue_purchase_browser_capability(order_created_at=self.now)
        foreign_order = self.make_order(
            public_number="FM-EFGH2345",
            event=self.event,
            photo=self.photo,
            browser_digest=foreign_capability.token_sha256,
            status=Order.Status.PAID,
        )

        for order, photo_id, token in (
            (foreign_order, self.photo.pk, self.purchase_capability.token),
            (self.order, self.unpurchased_photo.pk, self.purchase_capability.token),
            (self.order, self.other_event_photo.pk, self.purchase_capability.token),
        ):
            with self.subTest(order=order.public_number, photo_id=photo_id):
                with self.assertRaises(delivery.PurchasedOriginalDenied):
                    self.issue_with_browser(
                        delivery,
                        storage,
                        order=order,
                        photo_id=photo_id,
                        purchase_browser_token=token,
                    )

        self.assertEqual(storage.signed_requests, [])
        self.assertFalse(DownloadGrantAudit.objects.exists())

    def test_nonpaid_order_states_never_reach_signing(self) -> None:
        """Pending, superseded, or canceled snapshots are not entitlements despite a capability."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()

        for index, status in enumerate(
            (Order.Status.PENDING, Order.Status.SUPERSEDED, Order.Status.CANCELED), start=1
        ):
            capability = issue_purchase_browser_capability(order_created_at=self.now)
            order = self.make_order(
                public_number=f"FM-JKLM234{index + 1}",
                event=self.event,
                photo=self.photo,
                browser_digest=capability.token_sha256,
                status=status,
            )
            with self.subTest(status=status):
                with self.assertRaises(delivery.PurchasedOriginalDenied):
                    self.issue_with_browser(
                        delivery,
                        storage,
                        order=order,
                        purchase_browser_token=capability.token,
                    )

        self.assertEqual(storage.signed_requests, [])
        self.assertFalse(DownloadGrantAudit.objects.exists())

    def test_paid_item_survives_unpublication_and_hidden_watermark_presentation(self) -> None:
        """Rechecking public gallery eligibility would wrongly erase paid fulfillment."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()
        self.event.publication_status = Event.PublicationStatus.UNAVAILABLE
        self.event.save(update_fields=["publication_status"])

        download = self.issue_with_browser(delivery, storage)

        self.assertEqual(download.signed_url, "https://storage.example.test/purchased")
        self.assertEqual(storage.signed_requests[0][0], self.photo.original_key)

    def test_png_original_uses_the_existing_safe_attachment_filename_contract(self) -> None:
        """Using the source filename or wrong suffix could cause an unsafe downloaded name."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()
        png_photo = self.make_private_photo(
            event=self.event,
            photo_id="paid-png-photo",
            original_key="originals/33333333333333333333333333333333",
            original_content_type="image/png",
        )
        png_capability = issue_purchase_browser_capability(order_created_at=self.now)
        png_order = self.make_order(
            public_number="FM-NPQR2345",
            event=self.event,
            photo=png_photo,
            browser_digest=png_capability.token_sha256,
            status=Order.Status.PAID,
        )

        self.issue_with_browser(
            delivery,
            storage,
            order=png_order,
            photo_id=png_photo.pk,
            purchase_browser_token=png_capability.token,
        )

        self.assertEqual(
            storage.signed_requests,
            [(png_photo.original_key, "findme-photo-paid-png-photo.png")],
        )

    def test_revoked_grant_never_reaches_signing(self) -> None:
        """Checking only a signature would leave a revoked permanent link usable."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()
        revoke_order_access_grant(self.grant)

        with self.assertRaises(delivery.PurchasedOriginalDenied):
            self.issue_with_grant(delivery, storage)

        self.assertEqual(storage.signed_requests, [])
        self.assertFalse(DownloadGrantAudit.objects.exists())

    def test_cart_token_and_public_order_number_are_never_download_authority(self) -> None:
        """Selection and support identifiers must not substitute for a purchase capability."""
        delivery = load_original_delivery()
        storage = _RecordingStorage()
        self.assertNotEqual(
            browser_token_sha256(self.cart_token),
            self.order.purchase_browser_token_sha256,
        )

        for claimed_token in (self.cart_token, self.order.public_number):
            with self.subTest(claimed_token=claimed_token):
                with self.assertRaises(delivery.PurchasedOriginalDenied):
                    self.issue_with_browser(
                        delivery,
                        storage,
                        purchase_browser_token=claimed_token,
                    )

        self.assertEqual(storage.signed_requests, [])
        self.assertFalse(DownloadGrantAudit.objects.exists())

    def test_missing_or_mismatched_exact_object_fails_safely_and_opens_one_attention(self) -> None:
        """A bad original must not fall back to a preview, another photo, or a signed URL."""
        delivery = load_original_delivery()

        for failure in (ObjectMissing(), ObjectMismatch()):
            with self.subTest(failure=failure.code):
                storage = _RecordingStorage(failure)
                with self.assertRaises(delivery.PurchasedOriginalUnavailable) as caught:
                    self.issue_with_grant(delivery, storage)
                self.assertNotIn(self.photo.original_key, str(caught.exception))
                self.assertEqual(
                    storage.signed_requests,
                    [(self.photo.original_key, "findme-photo-paid-photo.jpg")],
                )
                self.assertNotIn(
                    self.unpurchased_photo.original_key,
                    [key for key, _filename in storage.signed_requests],
                )

        attention = CommerceAttention.objects.get(
            kind=CommerceAttention.Kind.ORIGINAL_MISSING,
            subject=f"order-item:{self.order.items.get().pk}",
        )
        self.assertEqual(attention.order_id, self.order.pk)
        self.assertEqual(CommerceAttention.objects.count(), 1)
        self.assertFalse(DownloadGrantAudit.objects.exists())

    def test_storage_unavailability_fails_safely_without_claiming_the_original_is_missing(
        self,
    ) -> None:
        """An outage is not evidence that the durable paid original has disappeared."""
        delivery = load_original_delivery()
        storage = _RecordingStorage(StorageUnavailable())

        with self.assertRaises(delivery.PurchasedOriginalUnavailable):
            self.issue_with_grant(delivery, storage)

        self.assertEqual(CommerceAttention.objects.count(), 0)
        self.assertFalse(DownloadGrantAudit.objects.exists())
