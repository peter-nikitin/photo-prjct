from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from commerce.models import Cart, CartItem


class CleanupExpiredCartsCommandTests(TestCase):
    def test_command_deletes_at_most_the_requested_expired_cart_rows_and_is_repeatable(
        self,
    ) -> None:
        event = Event.objects.create(
            name="Cleanup event",
            slug="cleanup-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        expired = [
            Cart.objects.create(
                browser_token_sha256=f"{index:064x}",
                event=event,
                expires_at=timezone.now() - timedelta(seconds=index + 1),
            )
            for index in range(3)
        ]
        photo = Photo.objects.create(id="cleanup-photo", event=event, src="photos/cleanup.jpg")
        for cart in expired:
            CartItem.objects.create(cart=cart, photo=photo)
        active = Cart.objects.create(
            browser_token_sha256="f" * 64,
            event=event,
            expires_at=timezone.now() + timedelta(days=1),
        )

        output = StringIO()
        call_command("cleanup_expired_carts", "--limit", "2", stdout=output)
        remaining_expired = Cart.objects.filter(pk__in=[cart.pk for cart in expired]).count()
        call_command("cleanup_expired_carts", "--limit", "2")

        self.assertEqual(remaining_expired, 1)
        self.assertTrue(Cart.objects.filter(pk=active.pk).exists())
        self.assertEqual(Cart.objects.filter(expires_at__lte=timezone.now()).count(), 0)
        self.assertIn("Deleted 2 expired carts", output.getvalue())
        self.assertNotIn("Deleted 4 expired carts", output.getvalue())
