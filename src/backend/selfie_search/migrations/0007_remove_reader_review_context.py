"""Keep old-web columns until the guarded post-commit physical contraction."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("selfie_search", "0006_reader_review_context")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE selfie_search_selfiesearch "
                    "ALTER COLUMN reader_staff_eligible SET DEFAULT false, "
                    "ALTER COLUMN reader_comparison_requested SET DEFAULT false",
                    migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.RemoveField(model_name="selfiesearch", name="reader_staff_eligible"),
                migrations.RemoveField(
                    model_name="selfiesearch", name="reader_comparison_requested"
                ),
            ],
        ),
    ]
