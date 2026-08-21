from dataclasses import dataclass

from picflow.models import Event


@dataclass(frozen=True)
class CartPricing:
    unit_price_kopecks: int
    item_count: int
    total_kopecks: int


def calculate_cart_pricing(*, event: Event, item_count: int) -> CartPricing:
    unit_price = event.price_per_photo_kopecks
    if (
        event.access_type != Event.AccessType.PAID
        or not isinstance(unit_price, int)
        or isinstance(unit_price, bool)
        or unit_price <= 0
    ):
        raise ValueError("Cart pricing requires a paid event with a positive photo price.")
    if not isinstance(item_count, int) or isinstance(item_count, bool) or item_count < 0:
        raise ValueError("Cart item count must be a non-negative integer.")
    return CartPricing(
        unit_price_kopecks=unit_price,
        item_count=item_count,
        total_kopecks=unit_price * item_count,
    )


def format_rub(amount_kopecks: int) -> str:
    if not isinstance(amount_kopecks, int) or isinstance(amount_kopecks, bool):
        raise TypeError("RUB amounts must use integer kopecks.")
    rubles, kopecks = divmod(amount_kopecks, 100)
    if kopecks == 0:
        return f"{rubles} ₽"
    return f"{rubles},{kopecks:02d} ₽"
