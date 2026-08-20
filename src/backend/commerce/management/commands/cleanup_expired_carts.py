from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from commerce.models import Cart


class Command(BaseCommand):
    help = "Delete a bounded batch of expired anonymous carts."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=1000)

    def handle(self, *args, **options) -> None:
        limit = options["limit"]
        if limit <= 0:
            raise CommandError("--limit must be a positive integer.")
        with transaction.atomic():
            cart_ids = list(
                Cart.objects.filter(expires_at__lte=timezone.now())
                .order_by("expires_at", "created_at", "pk")
                .values_list("pk", flat=True)[:limit]
            )
            deleted, _ = Cart.objects.filter(pk__in=cart_ids).delete()
        self.stdout.write(f"Deleted {deleted} expired carts")
