from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from commerce.models import (
    Cart,
    CartItem,
    CommerceAttention,
    DownloadGrantAudit,
    EmailDelivery,
    Order,
    OrderAccessGrant,
    PaymentAttempt,
    PaymentEvidence,
)


class CartModelContractTests(SimpleTestCase):
    def test_cart_persists_only_a_valid_digest_and_never_displays_it(self) -> None:
        field_names = {field.name for field in Cart._meta.get_fields()}
        digest = "a" * 64
        cart = Cart(id=7, browser_token_sha256=digest)

        self.assertEqual(Cart._meta.get_field("browser_token_sha256").max_length, 64)
        Cart._meta.get_field("browser_token_sha256").run_validators(digest)
        for invalid_digest in ("A" * 64, "a" * 63, "g" * 64):
            with self.subTest(digest=invalid_digest):
                with self.assertRaises(ValidationError):
                    Cart._meta.get_field("browser_token_sha256").run_validators(invalid_digest)

        self.assertTrue(
            {"browser_token_sha256", "event", "expires_at", "created_at"}.issubset(field_names)
        )
        self.assertTrue(
            {"browser_token", "raw_token", "currency", "subtotal", "total", "price"}.isdisjoint(
                field_names
            )
        )
        self.assertNotIn(digest, str(cart))
        self.assertNotIn(digest, repr(cart))

    def test_cart_item_has_only_selection_and_addition_order_state(self) -> None:
        field_names = {field.name for field in CartItem._meta.get_fields()}

        self.assertTrue({"cart", "photo", "added_at"}.issubset(field_names))
        self.assertTrue(
            {"quantity", "unit_price", "price", "subtotal", "currency"}.isdisjoint(field_names)
        )
        self.assertEqual(CartItem._meta.ordering, ["added_at", "photo_id"])
        self.assertEqual(
            CartItem._meta.get_field("cart").remote_field.on_delete.__name__,
            "CASCADE",
        )
        self.assertEqual(
            CartItem._meta.get_field("photo").remote_field.on_delete.__name__,
            "CASCADE",
        )

    def test_cart_item_string_uses_only_cart_and_photo_identifiers(self) -> None:
        digest = "a" * 64
        item = CartItem(
            cart=Cart(id=7, browser_token_sha256=digest),
            photo_id="PHOTO-1",
        )

        self.assertEqual(str(item), "Cart 7 / photo PHOTO-1")
        self.assertNotIn(digest, str(item))
        self.assertNotIn(digest, repr(item))


class CommerceIndexNameContractTests(SimpleTestCase):
    def test_explicit_commerce_index_names_fit_all_supported_database_limits(self) -> None:
        commerce_models = (
            Order,
            PaymentAttempt,
            PaymentEvidence,
            OrderAccessGrant,
            EmailDelivery,
            CommerceAttention,
            DownloadGrantAudit,
        )
        index_names = tuple(
            index.name for model in commerce_models for index in model._meta.indexes if index.name
        )

        self.assertTrue(index_names)
        self.assertTrue(all(len(name) <= 30 for name in index_names), index_names)


class CartModelTests(TestCase):
    def make_event(self, *, name: str) -> Event:
        return Event.objects.create(
            name=name,
            slug=name.lower().replace(" ", "-"),
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )

    def test_token_digest_and_event_identify_one_cart(self) -> None:
        first_event = self.make_event(name="First event")
        second_event = self.make_event(name="Second event")
        digest = "a" * 64
        expiry = timezone.now() + timedelta(days=30)
        Cart.objects.create(
            browser_token_sha256=digest,
            event=first_event,
            expires_at=expiry,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Cart.objects.create(
                browser_token_sha256=digest,
                event=first_event,
                expires_at=expiry,
            )

        Cart.objects.create(
            browser_token_sha256=digest,
            event=second_event,
            expires_at=expiry,
        )
        Cart.objects.create(
            browser_token_sha256="b" * 64,
            event=first_event,
            expires_at=expiry,
        )
        self.assertEqual(Cart.objects.count(), 3)

    def test_cart_positions_are_unique_and_ordered_by_addition_then_photo_id(self) -> None:
        event = self.make_event(name="Ordered event")
        cart = Cart.objects.create(
            browser_token_sha256="a" * 64,
            event=event,
            expires_at=timezone.now() + timedelta(days=30),
        )
        photos = {
            photo_id: Photo.objects.create(
                id=photo_id,
                event=event,
                src=f"photos/{photo_id}.jpg",
            )
            for photo_id in ("A", "B", "C")
        }
        same_time = timezone.now()
        CartItem.objects.create(cart=cart, photo=photos["B"], added_at=same_time)
        CartItem.objects.create(cart=cart, photo=photos["A"], added_at=same_time)
        CartItem.objects.create(
            cart=cart,
            photo=photos["C"],
            added_at=same_time - timedelta(seconds=1),
        )

        self.assertEqual(
            list(cart.items.values_list("photo_id", flat=True)),
            ["C", "A", "B"],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CartItem.objects.create(cart=cart, photo=photos["A"])

    def test_cart_item_rejects_a_photo_from_another_event(self) -> None:
        cart_event = self.make_event(name="Cart event")
        other_event = self.make_event(name="Other photo event")
        cart = Cart.objects.create(
            browser_token_sha256="a" * 64,
            event=cart_event,
            expires_at=timezone.now() + timedelta(days=30),
        )
        foreign_photo = Photo.objects.create(
            id="FOREIGN",
            event=other_event,
            src="photos/foreign.jpg",
        )

        with self.assertRaises(ValidationError) as raised:
            CartItem(cart=cart, photo=foreign_photo).full_clean()

        self.assertIn("photo", raised.exception.message_dict)
