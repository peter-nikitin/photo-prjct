from unittest.mock import patch

from django.test import TestCase
from picflow.models import Event, Photo

from commerce.models import Order, OrderItem
from commerce.order_numbers import ORDER_NUMBER_ALPHABET, generate_order_public_number


class OrderPublicNumberTests(TestCase):
    """The breaks caught here would expose predictable or authority-bearing order references."""

    def test_generated_number_has_the_exact_support_reference_format(self) -> None:
        """A changed prefix or ambiguous character could make support references unsafe."""
        number = generate_order_public_number()

        self.assertRegex(number, rf"^FM-[{ORDER_NUMBER_ALPHABET}]{{8}}$")
        self.assertEqual(len(number), 11)
        self.assertTrue(set(number.removeprefix("FM-")).issubset(set(ORDER_NUMBER_ALPHABET)))

    def test_generator_retries_a_database_collision(self) -> None:
        """A unique-index collision must not reject an otherwise valid checkout snapshot."""
        event = Event.objects.create(
            name="Number event",
            slug="number-event",
            start_date="2026-08-20",
            end_date="2026-08-20",
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        photo = Photo.objects.create(
            id="number-order-photo",
            event=event,
            src="photos/number-order.jpg",
        )
        order = Order.objects.create(
            public_number="FM-ABCDEFGH",
            event=event,
            checkout_email="buyer@example.test",
            total_kopecks=30000,
        )
        OrderItem.objects.create(
            order=order,
            photo=photo,
            photo_public_id=photo.pk,
            unit_price_kopecks=30000,
            quantity=1,
            line_total_kopecks=30000,
        )

        with patch(
            "commerce.order_numbers.secrets.choice",
            side_effect=[*"ABCDEFGH", *"JKLMNPQR"],
        ):
            self.assertEqual(generate_order_public_number(), "FM-JKLMNPQR")

    def test_numbers_are_random_references_not_primary_key_sequences_or_access_authority(
        self,
    ) -> None:
        """A support number as a capability would expose originals by guessing."""
        first = generate_order_public_number()
        second = generate_order_public_number()
        order_field_names = {field.name for field in Order._meta.local_fields}

        self.assertNotEqual(first, second)
        self.assertTrue(
            {"access_secret", "access_token", "access_grant", "signature"}.isdisjoint(
                order_field_names
            )
        )
