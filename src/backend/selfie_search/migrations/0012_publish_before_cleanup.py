from django.db import migrations, models
from django.db.models.functions import Coalesce, Now


def publish_saved_searches(apps, schema_editor):
    Search = apps.get_model("selfie_search", "SelfieSearch")
    Search.objects.using(schema_editor.connection.alias).filter(status="cleanup_pending").update(
        status=models.Case(
            models.When(intended_terminal_status="", then=models.Value("failed")),
            default=models.F("intended_terminal_status"),
        ),
        terminal_at=Now(),
        matched_photo_count=Coalesce("final_matched_photo_count", 0),
    )


class Migration(migrations.Migration):
    dependencies = [("selfie_search", "0011_remove_legacy_gallery_queue_fields")]

    operations = [
        migrations.RunPython(publish_saved_searches, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="selfiesearch", name="selfie_search_intended_status_chk"
        ),
        migrations.RemoveField(model_name="selfiesearch", name="intended_terminal_status"),
        migrations.RemoveConstraint(model_name="selfiesearch", name="selfie_search_status_chk"),
        migrations.AlterField(
            model_name="selfiesearch",
            name="status",
            field=models.CharField(
                max_length=24,
                default="queued",
                choices=[
                    ("queued", "Queued"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                    ("no_face", "No face"),
                    ("multiple_faces", "Multiple faces"),
                    ("quality_rejected", "Quality rejected"),
                    ("search_unavailable", "Search unavailable"),
                    ("failed", "Failed"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="selfiesearchfeedback",
            name="source_status",
            field=models.CharField(
                max_length=24,
                choices=[
                    ("queued", "Queued"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                    ("no_face", "No face"),
                    ("multiple_faces", "Multiple faces"),
                    ("quality_rejected", "Quality rejected"),
                    ("search_unavailable", "Search unavailable"),
                    ("failed", "Failed"),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "queued",
                        "processing",
                        "ready",
                        "no_face",
                        "multiple_faces",
                        "quality_rejected",
                        "search_unavailable",
                        "failed",
                    )
                ),
                name="selfie_search_status_chk",
            ),
        ),
    ]
