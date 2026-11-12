from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("processing", "0019_pg_stat_statements"),
        ("processing", "0019_processing_job_last_success_index"),
    ]
    operations: list[migrations.operations.base.Operation] = []
