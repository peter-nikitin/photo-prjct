from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from picflow.models import Event, Photo

_SHA256_HEX_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Browser token digest must be 64 lowercase hexadecimal characters.",
)


class Cart(models.Model):
    browser_token_sha256 = models.CharField(max_length=64, validators=[_SHA256_HEX_VALIDATOR])
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="carts")
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("browser_token_sha256", "event"),
                name="commerce_cart_token_event_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(browser_token_sha256__regex=r"^[0-9a-f]{64}$"),
                name="commerce_cart_token_sha_chk",
            ),
        ]

    def __str__(self) -> str:
        return f"Cart {self.pk if self.pk is not None else 'unsaved'}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name="cart_items")
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["added_at", "photo_id"]
        constraints = [
            models.UniqueConstraint(
                fields=("cart", "photo"),
                name="commerce_cart_item_photo_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"Cart {self.cart_id} / photo {self.photo_id}"

    def clean(self) -> None:
        super().clean()
        if self.cart_id and self.photo_id and self.cart.event_id != self.photo.event_id:
            raise ValidationError({"photo": "The photo must belong to the cart event."})
