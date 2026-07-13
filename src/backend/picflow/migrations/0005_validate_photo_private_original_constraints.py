from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("picflow", "0004_photo_private_original_constraints")]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE picflow_photo VALIDATE CONSTRAINT "
                "picflow_photo_uploaded_by_fk;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE picflow_photo VALIDATE CONSTRAINT "
                "picflow_photo_legacy_or_private_chk;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
