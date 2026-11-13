from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("selfie_search", "0010_shared_face_search_queue")]

    operations = [
        migrations.RemoveField(
            model_name="selfiesearch",
            name="gallery_attempt_count",
        ),
        migrations.RemoveField(
            model_name="selfiesearch",
            name="gallery_available_at",
        ),
        migrations.RemoveField(
            model_name="selfiesearch",
            name="gallery_started_at",
        ),
    ]
