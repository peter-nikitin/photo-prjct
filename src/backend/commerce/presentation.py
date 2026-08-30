from collections.abc import Collection, Sequence
from dataclasses import dataclass

from django.core.paginator import Page
from picflow.gallery import GalleryPhoto, GalleryPhotoFactory, MediaUrlBuilder

from commerce.models import Order, OrderItem
from commerce.pricing import format_rub
from commerce.services import CartSnapshot


@dataclass(frozen=True)
class CartPhotoPresentation:
    photo: GalleryPhoto
    selected: bool
    unit_price_kopecks: int
    unit_price_display: str


@dataclass(frozen=True)
class CartPresentation:
    photos: tuple[CartPhotoPresentation, ...]
    item_count: int
    unit_price_kopecks: int
    unit_price_display: str
    total_kopecks: int
    total_display: str
    pruned: bool
    mutation_locked: bool
    pending_order_public_number: str | None


@dataclass(frozen=True)
class OrderPhotoPresentation:
    photo: GalleryPhoto
    unit_price_display: str


@dataclass(frozen=True)
class OrderPresentation:
    public_number: str
    created_at_display: str
    event_name: str
    status: str
    status_display: str
    total_display: str
    masked_delivery_email: str
    item_count: int
    photos: tuple[OrderPhotoPresentation, ...]


def order_presentation(
    *,
    order: Order,
    items_page: Page[OrderItem],
    media_url_builder: MediaUrlBuilder | None = None,
) -> OrderPresentation:
    """Present the immutable customer-safe Order facts without payment/provider evidence."""
    status_display = {
        Order.Status.PENDING: "Проверяем оплату",
        Order.Status.SUPERSEDED: "Проверяем оплату",
        Order.Status.PAID: "Заказ оплачен",
        Order.Status.CANCELED: "Оплата не завершена",
    }[order.status]
    return OrderPresentation(
        public_number=order.public_number,
        created_at_display=order.created_at.strftime("%d.%m.%Y"),
        event_name=order.event.name,
        status=order.status,
        status_display=status_display,
        total_display=format_rub(order.total_kopecks),
        masked_delivery_email=_mask_email(order.delivery_email),
        item_count=items_page.paginator.count,
        photos=tuple(
            OrderPhotoPresentation(
                photo=GalleryPhotoFactory.from_photo(
                    photo=item.photo,
                    event_slug=order.event.slug,
                    media_url_builder=media_url_builder,
                ),
                unit_price_display=format_rub(item.unit_price_kopecks),
            )
            for item in items_page.object_list
        ),
    )


def _mask_email(value: str) -> str:
    local_part, separator, domain = value.partition("@")
    if not separator:
        return "***"
    if len(local_part) <= 1:
        masked_local_part = "***"
    elif len(local_part) == 2:
        masked_local_part = f"{local_part[0]}***"
    else:
        masked_local_part = f"{local_part[0]}***{local_part[-1]}"
    return f"{masked_local_part}@{domain}"


def cart_presentation_for_photos(
    *,
    snapshot: CartSnapshot,
    photos: Sequence[GalleryPhoto],
    eligible_photo_ids: Collection[str],
) -> CartPresentation:
    """Combine authoritative cart state with already-authorized gallery media."""
    eligible = frozenset(eligible_photo_ids)
    selected = frozenset(snapshot.photo_ids)
    unit_price_display = format_rub(snapshot.unit_price_kopecks)
    return CartPresentation(
        photos=tuple(
            CartPhotoPresentation(
                photo=photo,
                selected=photo.photo_id in selected,
                unit_price_kopecks=snapshot.unit_price_kopecks,
                unit_price_display=unit_price_display,
            )
            for photo in photos
            if photo.photo_id in eligible
        ),
        item_count=snapshot.item_count,
        unit_price_kopecks=snapshot.unit_price_kopecks,
        unit_price_display=unit_price_display,
        total_kopecks=snapshot.total_kopecks,
        total_display=format_rub(snapshot.total_kopecks),
        pruned=snapshot.pruned,
        mutation_locked=snapshot.mutation_locked,
        pending_order_public_number=snapshot.pending_order_public_number,
    )
