from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("picflow", "0013_event_photo_price"),
    ]

    operations = [
        migrations.AddField(
            model_name="photo",
            name="is_hidden",
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
