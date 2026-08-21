from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from picflow.models import Event, Photo

from commerce.order_numbers import generate_order_public_number

_SHA256_HEX_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Browser token digest must be 64 lowercase hexadecimal characters.",
)


def _require_unchanged(instance: models.Model, fields: tuple[str, ...], message: str) -> None:
    if instance._state.adding:
        return
    previous = instance.__class__.objects.filter(pk=instance.pk).values(*fields).first()
    if previous and any(previous[field] != getattr(instance, field) for field in fields):
        raise ValidationError(message)


def _require_write_once(
    instance: models.Model,
    fields: tuple[str, ...],
    message: str,
) -> None:
    if instance._state.adding:
        return
    previous = instance.__class__.objects.filter(pk=instance.pk).values(*fields).first()
    if previous and any(
        previous[field] not in ("", None) and previous[field] != getattr(instance, field)
        for field in fields
    ):
        raise ValidationError(message)


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


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUPERSEDED = "superseded", "Superseded"
        PAID = "paid", "Paid"
        CANCELED = "canceled", "Canceled"

    public_number = models.CharField(
        max_length=11,
        unique=True,
        default=generate_order_public_number,
    )
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="orders")
    checkout_email = models.EmailField()
    delivery_email = models.EmailField()
    total_kopecks = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="RUB")
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(currency="RUB"),
                name="commerce_order_currency_rub_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("pending", "superseded", "paid", "canceled")),
                name="commerce_order_status_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(total_kopecks__gt=0),
                name="commerce_order_total_positive_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    public_number__regex=r"^FM-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$"
                ),
                name="commerce_order_public_number_format_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="paid", paid_at__isnull=False)
                    | models.Q(
                        status__in=("pending", "superseded", "canceled"),
                        paid_at__isnull=True,
                    )
                ),
                name="commerce_order_paid_time_chk",
            ),
        ]
        indexes = [models.Index(fields=("event", "status"), name="commerce_order_event_status_idx")]

    def __str__(self) -> str:
        return self.public_number

    def save(self, *args, **kwargs) -> None:
        if self._state.adding and not self.delivery_email:
            self.delivery_email = self.checkout_email
        _require_unchanged(
            self,
            ("public_number", "event_id", "checkout_email", "total_kopecks", "currency"),
            "Order commercial snapshot is immutable.",
        )
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="items")
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="order_items")
    photo_public_id = models.CharField(max_length=32)
    unit_price_kopecks = models.PositiveIntegerField()
    quantity = models.PositiveSmallIntegerField(default=1)
    line_total_kopecks = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("order", "photo"),
                name="commerce_order_item_order_photo_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity=1),
                name="commerce_order_item_quantity_one_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(line_total_kopecks=models.F("unit_price_kopecks")),
                name="commerce_order_item_line_total_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(photo_public_id=models.F("photo")),
                name="commerce_order_item_photo_public_id_chk",
            ),
        ]

    def __str__(self) -> str:
        return f"Order {self.order_id} / photo {self.photo_id}"

    def save(self, *args, **kwargs) -> None:
        _require_unchanged(
            self,
            (
                "order_id",
                "photo_id",
                "photo_public_id",
                "unit_price_kopecks",
                "quantity",
                "line_total_kopecks",
            ),
            "Order items are immutable.",
        )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        if not self._state.adding and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Order items are immutable.")
        return super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.order_id and self.photo_id and self.order.event_id != self.photo.event_id:
            raise ValidationError({"photo": "The photo must belong to the order event."})
        if self.photo_id and self.photo_public_id != self.photo_id:
            raise ValidationError({"photo_public_id": "The public photo id must match the photo."})


class PaymentAttempt(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"
        CONFLICT = "conflict", "Conflict"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payment_attempts")
    amount_kopecks = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="RUB")
    adapter_key = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    provider_payment_id = models.CharField(max_length=255, blank=True)
    confirmation_url = models.URLField(max_length=2000, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    terminal_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(currency="RUB"),
                name="commerce_payment_attempt_currency_rub_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "pending",
                        "succeeded",
                        "canceled",
                        "expired",
                        "failed",
                        "conflict",
                    )
                ),
                name="commerce_payment_attempt_status_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_kopecks__gt=0),
                name="commerce_payment_attempt_amount_positive_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="pending", terminal_at__isnull=True)
                    | models.Q(
                        status__in=("succeeded", "canceled", "expired", "failed", "conflict"),
                        terminal_at__isnull=False,
                    )
                ),
                name="commerce_payment_attempt_terminal_time_chk",
            ),
            models.UniqueConstraint(
                fields=("idempotency_key",),
                name="commerce_payment_attempt_idempotency_uniq",
            ),
            models.UniqueConstraint(
                fields=("provider_payment_id",),
                condition=~models.Q(provider_payment_id=""),
                name="commerce_payment_attempt_provider_uniq",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=models.Q(status="pending"),
                name="commerce_payment_attempt_one_pending_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("order", "status"),
                name="commerce_payment_attempt_order_status_idx",
            )
        ]

    def __str__(self) -> str:
        return f"Payment attempt {self.pk if self.pk is not None else 'unsaved'}"

    def save(self, *args, **kwargs) -> None:
        _require_unchanged(
            self,
            (
                "order_id",
                "amount_kopecks",
                "currency",
                "adapter_key",
                "idempotency_key",
            ),
            "Payment attempt payment evidence is immutable.",
        )
        _require_write_once(
            self,
            ("provider_payment_id", "confirmation_url", "expires_at"),
            "Payment attempt provider response fields are write-once.",
        )
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.order_id and (
            self.amount_kopecks != self.order.total_kopecks or self.currency != self.order.currency
        ):
            raise ValidationError("Payment attempt amount and currency must match its order.")


class PaymentEvidence(models.Model):
    class Source(models.TextChoices):
        NOTIFICATION = "notification", "Notification"
        STATUS_FETCH = "status_fetch", "Status fetch"

    payment_attempt = models.ForeignKey(
        PaymentAttempt, on_delete=models.PROTECT, related_name="evidence"
    )
    source = models.CharField(max_length=16, choices=Source)
    provider_event_id = models.CharField(max_length=255, blank=True)
    normalized_status = models.CharField(max_length=10, choices=PaymentAttempt.Status)
    amount_kopecks = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="RUB")
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(source__in=("notification", "status_fetch")),
                name="commerce_payment_evidence_source_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    normalized_status__in=(
                        "pending",
                        "succeeded",
                        "canceled",
                        "expired",
                        "failed",
                        "conflict",
                    )
                ),
                name="commerce_payment_evidence_status_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(currency="RUB"),
                name="commerce_payment_evidence_currency_rub_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_kopecks__gt=0),
                name="commerce_payment_evidence_amount_positive_chk",
            ),
        ]
        indexes = [
            models.Index(
                fields=("payment_attempt", "observed_at"),
                name="commerce_payment_evidence_attempt_time_idx",
            )
        ]

    def __str__(self) -> str:
        return f"Payment evidence {self.pk if self.pk is not None else 'unsaved'}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Payment evidence is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        if not self._state.adding and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Payment evidence is append-only.")
        return super().delete(*args, **kwargs)
