import importlib
import importlib.util
from datetime import date

from django.contrib.auth import get_user_model
from django.core.paginator import InvalidPage
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from commerce.identity import browser_token_sha256
from commerce.models import Order, OrderItem


def load_order_items():
    assert importlib.util.find_spec("commerce.order_items") is not None, (
        "commerce.order_items must own paid Order pagination"
    )
    return importlib.import_module("commerce.order_items")


class OrderItemPageTests(TestCase):
    """The breaks caught here would change or broaden the visible paid batch."""

    def setUp(self) -> None:
        photographer = get_user_model().objects.create_user(username="order-page-photographer")
        event = Event.objects.create(
            name="Order page event",
            slug="order-page-event",
            start_date=date(2026, 8, 30),
            end_date=date(2026, 8, 30),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.order = Order.objects.create(
            public_number="FM-ABCD2345",
            event=event,
            purchase_browser_token_sha256=browser_token_sha256(
                "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
            ),
            checkout_email="buyer@example.test",
            total_kopecks=102 * 30000,
            status=Order.Status.PAID,
            paid_at=timezone.now(),
        )
        photos = [
            Photo(
                id=f"page-photo-{number:03d}",
                event=event,
                uploaded_by=photographer,
                original_key=f"private/page-photo-{number:03d}",
                original_filename=f"camera-{102 - number:03d}.jpg",
                original_size=10,
                original_content_type="image/jpeg",
                uploaded_at=timezone.now(),
                processing_generation=(Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1),
                gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
            )
            for number in range(102)
        ]
        Photo.objects.bulk_create(reversed(photos))
        OrderItem.objects.bulk_create(
            OrderItem(
                order=self.order,
                photo=photo,
                photo_public_id=photo.pk,
                unit_price_kopecks=30000,
                line_total_kopecks=30000,
            )
            for photo in reversed(photos)
        )

    def test_pages_are_fixed_to_100_items_in_stable_photo_id_order(self) -> None:
        pagination = load_order_items()

        first = pagination.order_item_page(order=self.order, page_number=None)
        second = pagination.order_item_page(order=self.order, page_number="2")

        first_items = list(first.object_list)
        self.assertEqual(len(first_items), 100)
        self.assertEqual(first_items[0].photo_id, "page-photo-000")
        self.assertEqual(first_items[-1].photo_id, "page-photo-099")
        self.assertEqual(
            tuple(item.photo_id for item in second.object_list),
            ("page-photo-100", "page-photo-101"),
        )
        self.assertEqual(first.paginator.count, 102)
        self.assertEqual(first.paginator.per_page, 100)

    def test_invalid_and_out_of_range_pages_fail_closed(self) -> None:
        pagination = load_order_items()

        for page_number in ("", "wrong", "0", "-1", "3"):
            with self.subTest(page_number=page_number):
                with self.assertRaises(InvalidPage):
                    pagination.order_item_page(order=self.order, page_number=page_number)
