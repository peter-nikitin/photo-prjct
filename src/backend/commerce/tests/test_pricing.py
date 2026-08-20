from typing import Any, cast

from django.test import SimpleTestCase
from picflow.models import Event

from commerce.pricing import calculate_cart_pricing, format_rub


class CartPricingTests(SimpleTestCase):
    def test_pricing_uses_the_current_event_price_with_exact_integer_arithmetic(self) -> None:
        event = Event(
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )

        initial = calculate_cart_pricing(event=event, item_count=3)
        event.price_per_photo_kopecks = 45075
        changed = calculate_cart_pricing(event=event, item_count=3)

        self.assertEqual(initial.unit_price_kopecks, 30000)
        self.assertEqual(initial.item_count, 3)
        self.assertEqual(initial.total_kopecks, 90000)
        self.assertEqual(changed.unit_price_kopecks, 45075)
        self.assertEqual(changed.total_kopecks, 135225)

    def test_rub_formatting_uses_whole_rubles_or_a_comma_and_two_kopeck_digits(self) -> None:
        cases = (
            (30000, "300 ₽"),
            (12345, "123,45 ₽"),
            (1, "0,01 ₽"),
        )

        for kopecks, expected in cases:
            with self.subTest(kopecks=kopecks):
                self.assertEqual(format_rub(kopecks), expected)

        with self.assertRaises(TypeError):
            format_rub(cast(Any, 300.0))
