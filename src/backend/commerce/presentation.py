from collections.abc import Collection, Sequence
from dataclasses import dataclass

from picflow.gallery import GalleryPhoto

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
    )
