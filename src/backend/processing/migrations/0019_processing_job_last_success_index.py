from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("processing", "0018_retire_legacy_vector_state")]
    operations = [
        AddIndexConcurrently(
            model_name="processingjob",
            index=models.Index(
                fields=["-completed_at"],
                condition=models.Q(status="succeeded", completed_at__isnull=False),
                name="proc_job_last_success_idx",
            ),
        ),
    ]
