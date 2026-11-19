from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="SearchFeedback",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("submission_id", models.UUIDField()),
                ("event_id", models.PositiveBigIntegerField()),
                ("photo_id", models.CharField(max_length=32)),
                (
                    "search_type",
                    models.CharField(
                        choices=[("selfie", "Selfie"), ("gallery", "Gallery face"), ("bib", "Bib")],
                        max_length=7,
                    ),
                ),
                (
                    "value",
                    models.CharField(
                        choices=[("present", "Present"), ("absent", "Absent")], max_length=7
                    ),
                ),
                ("search_context", models.JSONField()),
                ("legacy_attachment_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
