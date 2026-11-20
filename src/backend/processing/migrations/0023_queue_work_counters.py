from django.db import migrations, models


def create_counters(apps, schema_editor):
    counter = apps.get_model("processing", "QueueWorkCounter")
    queues = (
        "gallery-search",
        "selfie_face_search",
        "selfie",
        "photo",
        "import",
        "commerce",
        "photo_archive",
    )
    counter.objects.using(schema_editor.connection.alias).bulk_create(
        [counter(queue=queue) for queue in queues]
    )


class Migration(migrations.Migration):
    dependencies = [("processing", "0022_personappearancedetection")]

    operations = [
        migrations.CreateModel(
            name="QueueWorkCounter",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("queue", models.CharField(max_length=32)),
                ("processor", models.CharField(blank=True, default="", max_length=64)),
                ("waiting", models.BigIntegerField(default=0)),
                ("processing", models.BigIntegerField(default=0)),
                ("retry_wait", models.BigIntegerField(default=0)),
                ("paused", models.BigIntegerField(default=0)),
                ("initialized", models.BooleanField(default=False)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("queue", "processor"), name="queue_work_key_uniq"
                    ),
                ],
            },
        ),
        migrations.RunPython(create_counters, reverse_code=migrations.RunPython.noop),
    ]
