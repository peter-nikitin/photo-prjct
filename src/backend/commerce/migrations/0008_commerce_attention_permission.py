from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("commerce", "0007_payment_reconciliation_leases")]

    operations = [
        migrations.AlterModelOptions(
            name="commerceattention",
            options={"permissions": [("handle_attention", "Can handle Commerce attention")]},
        )
    ]
