from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("commerce", "0009_rename_commerce_indexes")]
    operations = [
        migrations.AddField(
            model_name="paymentattempt",
            name="initiation_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
