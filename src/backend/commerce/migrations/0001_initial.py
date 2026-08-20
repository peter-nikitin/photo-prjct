import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("picflow", "0013_event_photo_price")]

    operations = [
        migrations.CreateModel(
            name="Cart",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "browser_token_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator(
                                message=(
                                    "Browser token digest must be 64 lowercase hexadecimal "
                                    "characters."
                                ),
                                regex="^[0-9a-f]{64}$",
                            )
                        ],
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="carts",
                        to="picflow.event",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CartItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("added_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "cart",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="commerce.cart",
                    ),
                ),
                (
                    "photo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cart_items",
                        to="picflow.photo",
                    ),
                ),
            ],
            options={"ordering": ["added_at", "photo_id"]},
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.CheckConstraint(
                condition=models.Q(("browser_token_sha256__regex", "^[0-9a-f]{64}$")),
                name="commerce_cart_token_sha_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.UniqueConstraint(
                fields=("browser_token_sha256", "event"),
                name="commerce_cart_token_event_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                fields=("cart", "photo"),
                name="commerce_cart_item_photo_uniq",
            ),
        ),
    ]
