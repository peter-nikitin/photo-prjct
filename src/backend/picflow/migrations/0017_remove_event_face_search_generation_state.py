from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("picflow", "0016_gallery_media_projection")]

    # The canonical deployment migrates before replacing the old web process.
    # Keep the physical column until the guarded post-activation contraction.
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="event", name="face_search_generation")
            ],
            database_operations=[],
        )
    ]
