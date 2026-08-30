from typing import Literal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from feature_flags.registry import BULK_PHOTO_DOWNLOAD
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON
from ingestion.storage import ObjectMissing, OpenedObject, StorageUnavailable
from picflow.archive import ArchiveSourceMissing, ArchiveSourceUnavailable

from commerce.capabilities import create_order_access_grant, sign_order_access_grant
from commerce.models import CommerceAttention, DownloadGrantAudit
from commerce.tests.test_order_views import OrderViewFixture


class _Body:
    def __init__(self, chunks: list[bytes | Exception]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def read(self, _size: int | None = -1) -> bytes:
        value = next(self._chunks, b"")
        if isinstance(value, Exception):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class _Storage:
    def __init__(self, opened: list[OpenedObject | Exception] | None = None) -> None:
        self._opened = iter(opened or [])

    def open_final(self, *, key: str) -> OpenedObject:
        opened = next(self._opened)
        if isinstance(opened, Exception):
            raise opened
        return opened


def _opened(
    chunks: list[bytes | Exception],
    *,
    size: int = 10,
    content_type: Literal["image/jpeg", "image/png"] = "image/jpeg",
) -> OpenedObject:
    return OpenedObject(body=_Body(chunks), size=size, content_type=content_type)


@override_settings(
    COMMERCE_ORDER_ACCESS_SIGNING_SECRET="order-view-test-secret",
    COMMERCE_SUPPORT_CONTACT="support@example.test",
)
class PaidArchiveDeliveryTests(OrderViewFixture):
    def setUp(self) -> None:
        super().setUp()
        self.enable(purchase=FEATURE_FLAG_ON)
        self.feature_flag_states[BULK_PHOTO_DOWNLOAD] = FEATURE_FLAG_ON
        self.order = self.make_order(total_kopecks=60000)
        self.photo_ids = self.add_order_photos(self.order, count=1)

    def archive_url(self, *, page: int | None = None) -> str:
        url = reverse("commerce:order_archive", kwargs={"public_number": self.order.public_number})
        return f"{url}?page={page}" if page is not None else url

    def test_browser_archive_has_exact_name_headers_entries_audits_and_first_access(self) -> None:
        with (
            patch("commerce.views._archive_storage", return_value=_Storage()),
            patch("commerce.views.prepare_zip_archive", return_value=iter((b"zip",))) as prepare,
        ):
            response = self.client.get(self.archive_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="findme-photo-order-{self.order.public_number}.zip"',
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertNotIn("Content-Length", response)
        entries = prepare.call_args.kwargs["entries"]
        self.assertEqual(
            tuple(entry.photo_id for entry in entries),
            (self.photo.pk, self.photo_ids[0]),
        )
        self.assertEqual(DownloadGrantAudit.objects.count(), 2)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.first_customer_access_at)

    def test_active_grant_archive_is_exact_and_cross_order_signature_is_denied(self) -> None:
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
        other_order = self.make_order(public_number="FM-EFGH2345")
        self.client.cookies.pop("findme_purchase", None)

        with (
            patch("commerce.views._archive_storage", return_value=_Storage()),
            patch("commerce.views.prepare_zip_archive", return_value=iter((b"zip",))),
        ):
            allowed = self.client.get(grant_url)
            denied = self.client.get(
                reverse(
                    "commerce:grant_order_archive",
                    kwargs={
                        "public_number": other_order.public_number,
                        "grant_identifier": grant.pk,
                        "signature": signature,
                    },
                )
            )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(
            set(DownloadGrantAudit.objects.values_list("access_grant_id", flat=True)),
            {grant.pk},
        )

    def test_direct_endpoint_fails_closed_for_gate_invalid_page_and_nonpaid_order(self) -> None:
        with patch("commerce.views._archive_storage") as storage:
            self.feature_flag_states[BULK_PHOTO_DOWNLOAD] = FEATURE_FLAG_OFF
            off = self.client.get(self.archive_url())
            self.feature_flag_states[BULK_PHOTO_DOWNLOAD] = FEATURE_FLAG_ON
            invalid = self.client.get(self.archive_url(page=2))
            small_order = self.make_order(public_number="FM-JKLM2345")
            small = self.client.get(
                reverse(
                    "commerce:order_archive",
                    kwargs={"public_number": small_order.public_number},
                )
            )
            self.order.status = self.order.Status.PENDING
            self.order.paid_at = None
            self.order.save(update_fields=["status", "paid_at"])
            pending = self.client.get(self.archive_url())

        self.assertEqual(
            tuple(response.status_code for response in (off, invalid, small, pending)),
            (404, 404, 404, 404),
        )
        storage.assert_not_called()

    def test_first_missing_source_opens_attention_and_returns_private_503(self) -> None:
        with patch(
            "commerce.views._archive_storage",
            return_value=_Storage([ObjectMissing()]),
        ):
            response = self.client.get(self.archive_url())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        attention = CommerceAttention.objects.get()
        self.assertEqual(attention.kind, CommerceAttention.Kind.ORIGINAL_MISSING)
        self.assertEqual(attention.order_id, self.order.pk)
        self.assertEqual(
            attention.subject,
            f"order-item:{self.order.items.order_by('photo_id').first().pk}",
        )

    def test_first_open_and_first_read_unavailability_return_503_without_attention(self) -> None:
        storages = (
            _Storage([StorageUnavailable()]),
            _Storage([_opened([OSError("temporary read outage")])]),
        )

        for storage in storages:
            with self.subTest(storage=storage):
                with patch("commerce.views._archive_storage", return_value=storage):
                    response = self.client.get(self.archive_url())
                self.assertEqual(response.status_code, 503)

        self.assertFalse(CommerceAttention.objects.exists())

    def test_first_identity_mismatch_opens_exact_attention(self) -> None:
        with patch(
            "commerce.views._archive_storage",
            return_value=_Storage([_opened([], size=11)]),
        ):
            response = self.client.get(self.archive_url())

        self.assertEqual(response.status_code, 503)
        first_item = self.order.items.order_by("photo_id").first()
        self.assertEqual(
            CommerceAttention.objects.get().subject,
            f"order-item:{first_item.pk}",
        )

    def test_later_read_unavailability_aborts_without_attention(self) -> None:
        storage = _Storage(
            [
                _opened([b"x" * 10]),
                _opened([OSError("temporary later read outage")]),
            ]
        )
        with patch("commerce.views._archive_storage", return_value=storage):
            response = self.client.get(self.archive_url())

        self.assertEqual(response.status_code, 200)
        with self.assertRaises(ArchiveSourceUnavailable):
            b"".join(response.streaming_content)
        self.assertFalse(CommerceAttention.objects.exists())

    def test_later_read_missing_opens_exact_later_item_attention(self) -> None:
        storage = _Storage(
            [
                _opened([b"x" * 10]),
                _opened([ObjectMissing()]),
            ]
        )
        with patch("commerce.views._archive_storage", return_value=storage):
            response = self.client.get(self.archive_url())

        with self.assertRaises(ArchiveSourceMissing):
            b"".join(response.streaming_content)
        later_item = self.order.items.order_by("photo_id")[1]
        self.assertEqual(
            CommerceAttention.objects.get().subject,
            f"order-item:{later_item.pk}",
        )
