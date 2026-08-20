from django.db import migrations, models


def set_existing_paid_event_prices(apps, schema_editor) -> None:
    Event = apps.get_model("picflow", "Event")
    Event.objects.using(schema_editor.connection.alias).filter(access_type="paid").update(
        price_per_photo_kopecks=30000
    )


def clear_event_prices(apps, schema_editor) -> None:
    Event = apps.get_model("picflow", "Event")
    Event.objects.using(schema_editor.connection.alias).update(price_per_photo_kopecks=None)


class Migration(migrations.Migration):
    dependencies = [("picflow", "0012_paid_watermarked_photo_policy")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="price_per_photo_kopecks",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(set_existing_paid_event_prices, clear_event_prices),
        migrations.AddConstraint(
            model_name="event",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(access_type="free", price_per_photo_kopecks__isnull=True)
                    | models.Q(
                        access_type="paid",
                        price_per_photo_kopecks__isnull=False,
                        price_per_photo_kopecks__gt=0,
                    )
                ),
                name="picflow_event_access_price_chk",
            ),
        ),
    ]
