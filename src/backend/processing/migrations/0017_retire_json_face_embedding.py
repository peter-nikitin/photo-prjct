"""Retire model state; canonical deployment drops the table only after activation."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("processing", "0016_remove_processingattempt_worker_build")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="FaceEmbedding")],
        ),
    ]
