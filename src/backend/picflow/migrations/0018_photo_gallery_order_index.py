from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("picflow", "0017_remove_event_face_search_generation_state")]
    operations = [
        AddIndexConcurrently(
            model_name="photo",
            index=models.Index(
                fields=["event", "original_filename", "id"],
                name="picflow_photo_gallery_sort_idx",
                condition=models.Q(is_hidden=False, src="", original_key__isnull=False),
            ),
        )
    ]
