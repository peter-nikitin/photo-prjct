from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("processing", "0018_retire_legacy_vector_state")]
    operations = [CreateExtension("pg_stat_statements")]
