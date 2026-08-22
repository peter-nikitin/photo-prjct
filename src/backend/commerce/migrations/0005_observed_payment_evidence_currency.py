import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("commerce", "0004_order_originating_cart_digest")]

    operations = [
        migrations.AlterField(
            model_name="paymentevidence",
            name="currency",
            field=models.CharField(
                default="RUB",
                max_length=3,
                validators=[
                    django.core.validators.RegexValidator(
                        message=(
                            "Observed payment currency must be a three-letter uppercase ASCII code."
                        ),
                        regex="^[A-Z]{3}$",
                    )
                ],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="paymentevidence",
            name="commerce_payment_evidence_currency_rub_chk",
        ),
        migrations.AddConstraint(
            model_name="paymentevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="commerce_payment_evidence_currency_code_chk",
            ),
        ),
    ]
