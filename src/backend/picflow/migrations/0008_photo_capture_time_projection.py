import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("picflow", "0007_event_timezone"),
        ("processing", "0006_face_cluster_corpus"),
    ]

    operations = [
        migrations.AddField(
            model_name="photo",
            name="capture_time",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="photo",
            name="capture_time_source_attempt",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="processing.processingattempt",
            ),
        ),
        migrations.AddIndex(
            model_name="photo",
            index=models.Index(
                fields=["event", "capture_time"], name="picflow_photo_event_capture_time_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="photo",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("capture_time__isnull", True),
                        ("capture_time_source_attempt__isnull", True),
                    )
                    | models.Q(
                        ("capture_time__isnull", False),
                        ("capture_time_source_attempt__isnull", False),
                    )
                ),
                name="picflow_photo_capture_time_pair_chk",
            ),
        ),
    ]
