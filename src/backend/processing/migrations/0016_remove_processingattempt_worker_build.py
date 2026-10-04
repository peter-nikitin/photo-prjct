from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("processing", "0015_bulk_member_attempts")]

    operations = [migrations.RemoveField(model_name="processingattempt", name="worker_build")]
