from unittest.mock import Mock, patch

from django.test import Client
from django.urls import reverse
from feature_flags.states import FEATURE_FLAG_ON

from commerce.models import DownloadGrantAudit, EmailDelivery
from commerce.tests.test_order_views import OrderViewFixture


class PurchasedDownloadViewTests(OrderViewFixture):
    def download_url(self, order, photo_id: str) -> str:
        return reverse(
            "commerce:order_download",
            kwargs={"public_number": order.public_number, "photo_id": photo_id},
        )

    def resend_url(self, order) -> str:
        return reverse("commerce:order_resend", kwargs={"public_number": order.public_number})

    def test_paid_browser_can_download_only_the_exact_order_item_as_a_private_redirect(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        storage = Mock()
        storage.sign_final.return_value = "https://storage.test.invalid/signed-original"

        with patch("commerce.views._purchased_original_storage", return_value=storage):
            response = self.client.get(self.download_url(order, self.photo.pk))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://storage.test.invalid/signed-original")
        storage.sign_final.assert_called_once_with(
            key="private/checkout-photo.jpg",
            attachment_filename="findme-photo-checkout-photo.jpg",
        )
        self.assertEqual(DownloadGrantAudit.objects.count(), 1)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_cross_order_photo_is_sanitized_without_storage_signing_or_audit(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        storage = Mock()

        with patch("commerce.views._purchased_original_storage", return_value=storage):
            response = self.client.get(self.download_url(order, "another-photo"))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")
        storage.sign_final.assert_not_called()
        self.assertEqual(DownloadGrantAudit.objects.count(), 0)

    def test_resend_is_csrf_protected_and_rate_limited_by_order_not_session(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies["findme_purchase"] = self.cart_token
        rejected = csrf_client.post(self.resend_url(order))
        first = self.client.post(self.resend_url(order))
        another_browser = Client()
        another_browser.cookies["findme_purchase"] = self.cart_token
        second = another_browser.post(self.resend_url(order))

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(EmailDelivery.objects.filter(order=order).count(), 1)
        self.assertEqual(second["Cache-Control"], "private, no-store")
        self.assertEqual(second["Referrer-Policy"], "no-referrer")
