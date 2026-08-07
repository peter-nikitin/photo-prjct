from django.db import migrations, models


def set_event_nine_timezone(apps, schema_editor):
    Event = apps.get_model("picflow", "Event")
    Event.objects.filter(pk=9).update(timezone_name="Europe/Moscow")


def unset_event_nine_timezone(apps, schema_editor):
    Event = apps.get_model("picflow", "Event")
    Event.objects.filter(pk=9, timezone_name="Europe/Moscow").update(timezone_name=None)


class Migration(migrations.Migration):
    dependencies = [("picflow", "0006_photo_processing_policy")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="timezone_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.RunPython(set_event_nine_timezone, unset_event_nine_timezone),
    ]
