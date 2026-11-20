from django.db import migrations, models


def create_counters(apps, schema_editor):
    counter = apps.get_model("processing", "PhotoImportCounter")
    counter.objects.using(schema_editor.connection.alias).bulk_create(
        [counter(queue="photo_import_face"), counter(queue="photo_import_gallery")]
    )


class Migration(migrations.Migration):
    dependencies = [("processing", "0020_merge_photo_metrics_and_pg_stat_statements")]

    operations = [
        migrations.CreateModel(
            name="PhotoImportCounter",
            fields=[
                ("queue", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("pending", models.BigIntegerField(default=0)),
                ("failed", models.BigIntegerField(default=0)),
                ("initialized", models.BooleanField(default=False)),
            ],
        ),
        migrations.RunPython(create_counters, reverse_code=migrations.RunPython.noop),
    ]
