from types import SimpleNamespace
from typing import cast

from django.core.paginator import Paginator
from django.test import SimpleTestCase
from picflow.gallery import GalleryMedia, GalleryPhoto

from commerce.models import Order
from commerce.presentation import cart_presentation_for_photos, order_presentation
from commerce.services import CartSnapshot


def gallery_photo(photo_id: str) -> GalleryPhoto:
    return GalleryPhoto(
        photo_id=photo_id,
        preview_media_small=GalleryMedia(
            url=f"/events/run/photos/{photo_id}/media/preview-small/",
            variant="preview-small",
        ),
        preview_media_large=GalleryMedia(
            url=f"/events/run/photos/{photo_id}/media/preview-large/",
            variant="preview-large",
        ),
        download_url=None,
        alt=f"Photo {photo_id}",
    )


class CartPresentationTests(SimpleTestCase):
    """The breaks caught here expose ineligible media or derive prices outside Commerce."""

    def test_filters_to_current_eligible_ids_and_preserves_gallery_order(self) -> None:
        first = gallery_photo("first")
        legacy = gallery_photo("legacy")
        second = gallery_photo("second")
        snapshot = CartSnapshot(
            photo_ids=("second",),
            unit_price_kopecks=30000,
            item_count=1,
            total_kopecks=30000,
        )

        presentation = cart_presentation_for_photos(
            snapshot=snapshot,
            photos=(first, legacy, second),
            eligible_photo_ids=("first", "second"),
        )

        self.assertEqual(
            tuple(item.photo.photo_id for item in presentation.photos),
            ("first", "second"),
        )
        self.assertEqual(
            tuple(item.selected for item in presentation.photos),
            (False, True),
        )
        self.assertIs(presentation.photos[0].photo, first)
        self.assertIs(presentation.photos[1].photo, second)

    def test_uses_snapshot_count_prices_total_and_pruning_state_verbatim(self) -> None:
        snapshot = CartSnapshot(
            photo_ids=("selected", "off-page"),
            unit_price_kopecks=45075,
            item_count=2,
            total_kopecks=90150,
            pruned=True,
        )

        presentation = cart_presentation_for_photos(
            snapshot=snapshot,
            photos=(gallery_photo("selected"),),
            eligible_photo_ids=("selected",),
        )

        self.assertEqual(presentation.item_count, 2)
        self.assertEqual(presentation.unit_price_kopecks, 45075)
        self.assertEqual(presentation.unit_price_display, "450,75 ₽")
        self.assertEqual(presentation.total_kopecks, 90150)
        self.assertEqual(presentation.total_display, "901,50 ₽")
        self.assertTrue(presentation.pruned)
        self.assertEqual(presentation.photos[0].unit_price_display, "450,75 ₽")

    def test_empty_visible_page_keeps_the_authoritative_event_count(self) -> None:
        snapshot = CartSnapshot(
            photo_ids=("off-page",),
            unit_price_kopecks=30000,
            item_count=1,
            total_kopecks=30000,
        )

        presentation = cart_presentation_for_photos(
            snapshot=snapshot,
            photos=(),
            eligible_photo_ids=(),
        )

        self.assertEqual(presentation.photos, ())
        self.assertEqual(presentation.item_count, 1)
        self.assertEqual(presentation.total_display, "300 ₽")


class OrderPresentationTests(SimpleTestCase):
    def test_uses_total_item_count_but_only_current_page_photos(self) -> None:
        event = SimpleNamespace(name="Paged order", slug="paged-order")
        photos = tuple(
            SimpleNamespace(
                pk=f"photo-{number:03d}",
                event=event,
                gallery_media_policy="watermarked_preview_required",
                capture_time=None,
            )
            for number in range(101)
        )
        items = tuple(SimpleNamespace(photo=photo, unit_price_kopecks=30000) for photo in photos)
        page = Paginator(items, 100).page(2)
        order = SimpleNamespace(
            public_number="FM-ABCD2345",
            created_at=SimpleNamespace(strftime=lambda _format: "30.08.2026"),
            event=event,
            status="paid",
            total_kopecks=101 * 30000,
            delivery_email="buyer@example.test",
        )

        presentation = order_presentation(
            order=cast(Order, order),
            items_page=page,
            media_url_builder=lambda photo, variant: f"/{photo.pk}/{variant}/",
        )

        self.assertEqual(presentation.item_count, 101)
        self.assertEqual(
            tuple(item.photo.photo_id for item in presentation.photos),
            ("photo-100",),
        )
