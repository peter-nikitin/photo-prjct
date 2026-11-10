from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("selfie_search", "0008_selfiesearch_gallery_attempt_count_and_more")]
    operations = [
        migrations.AddField(
            model_name="selfiesearch",
            name="gallery_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
