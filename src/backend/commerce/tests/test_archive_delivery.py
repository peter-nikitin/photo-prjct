from datetime import timedelta
from unittest.mock import patch

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.registry import BULK_PHOTO_DOWNLOAD
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON
from ingestion.storage import StorageUnavailable
from picflow.models import PhotoArchiveJob

from commerce.capabilities import (
    create_order_access_grant,
    revoke_order_access_grant,
    sign_order_access_grant,
)
from commerce.models import DownloadGrantAudit
from commerce.tests.test_order_views import OrderViewFixture

_SIGNED_URL = "https://storage.example.test/archives/private.zip?signature=secret"


@override_settings(
    COMMERCE_ORDER_ACCESS_SIGNING_SECRET="order-view-test-secret",
    COMMERCE_SUPPORT_CONTACT="support@example.test",
)
class PaidArchiveDeliveryTests(OrderViewFixture):
    def setUp(self) -> None:
        super().setUp()
        self.enable(purchase=FEATURE_FLAG_ON)
        self.feature_flag_states[BULK_PHOTO_DOWNLOAD] = FEATURE_FLAG_ON
        self.order = self.make_order(status="pending", total_kopecks=60000)
        self.add_order_photos(self.order, count=1)
        for index, item in enumerate(self.order.items.select_related("photo"), start=1):
            item.photo.original_key = f"originals/{index:032x}"
            item.photo.save(update_fields=["original_key"])
        self.order.status = self.order.Status.PAID
        self.order.paid_at = timezone.now()
        self.order.save(update_fields=["status", "paid_at"])

    def archive_url(self) -> str:
        return reverse("commerce:order_archive", kwargs={"public_number": self.order.public_number})

    def mark_ready(self) -> None:
        PhotoArchiveJob.objects.update(
            status=PhotoArchiveJob.Status.READY,
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def test_paid_archive_audits_exact_items_when_accepting_request(self) -> None:
        created = self.client.post(self.archive_url())
        self.assertEqual(created.status_code, 202)
        status_url = created.json()["status_url"]
        self.assertEqual(self.client.get(status_url).json(), {"status": "queued"})
        self.assertCountEqual(
            DownloadGrantAudit.objects.values_list("order_item_id", flat=True),
            self.order.items.values_list("pk", flat=True),
        )

        self.mark_ready()
        self.assertEqual(self.client.get(status_url).json(), {"status": "ready"})
        download = f"{status_url}?download=1"
        with patch("picflow.archive_delivery.sign_archive_job", side_effect=StorageUnavailable):
            self.assertEqual(self.client.get(download).status_code, 503)
        with patch("picflow.archive_delivery.sign_archive_job", return_value=_SIGNED_URL):
            response = Client().get(download)
        self.assertRedirects(response, _SIGNED_URL, fetch_redirect_response=False)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertCountEqual(
            DownloadGrantAudit.objects.values_list("order_item_id", flat=True),
            self.order.items.values_list("pk", flat=True),
        )
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.first_customer_access_at)

    def test_only_initial_preparation_requires_an_active_order_grant(self) -> None:
        grant = create_order_access_grant(order=self.order, source="checkout")
        signature = sign_order_access_grant(grant=grant, signing_secret="order-view-test-secret")
        grant_url = reverse(
            "commerce:grant_order_archive",
            kwargs={
                "public_number": self.order.public_number,
                "grant_identifier": grant.pk,
                "signature": signature,
            },
        )
        self.client.cookies.pop("findme_purchase", None)
        response = self.client.post(grant_url)
        self.assertEqual(response.status_code, 202)
        self.mark_ready()
        download_url = f"{response.json()['status_url']}?download=1"
        self.assertEqual(self.client.post(self.archive_url()).status_code, 404)
        revoke_order_access_grant(grant)

        self.assertEqual(self.client.post(grant_url).status_code, 404)
        self.assertNotIn(signature, download_url)
        with patch("picflow.archive_delivery.sign_archive_job", return_value=_SIGNED_URL):
            self.assertRedirects(
                Client().get(download_url), _SIGNED_URL, fetch_redirect_response=False
            )

    def test_gate_and_invalid_page_prevent_enqueuing(self) -> None:
        self.feature_flag_states[BULK_PHOTO_DOWNLOAD] = FEATURE_FLAG_OFF
        self.assertEqual(self.client.post(self.archive_url()).status_code, 404)
        self.feature_flag_states[BULK_PHOTO_DOWNLOAD] = FEATURE_FLAG_ON
        self.assertEqual(self.client.post(f"{self.archive_url()}?page=2").status_code, 404)
        self.assertFalse(PhotoArchiveJob.objects.exists())

    def test_failed_archive_can_be_requested_again(self) -> None:
        created = self.client.post(self.archive_url())
        self.assertEqual(created.status_code, 202)
        PhotoArchiveJob.objects.update(status=PhotoArchiveJob.Status.FAILED)

        self.assertEqual(self.client.get(created.json()["status_url"]).json(), {"status": "failed"})
        retry = self.client.post(self.archive_url())
        self.assertEqual(retry.status_code, 202)
        self.assertNotEqual(retry.json()["status_url"], created.json()["status_url"])
        self.assertEqual(self.client.get(retry.json()["status_url"]).json(), {"status": "queued"})
