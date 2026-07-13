import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


BEGIN_LOCK_TIMEOUT = "BEGIN; SET LOCAL lock_timeout = '2s';"
COMMIT = "COMMIT;"


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("picflow", "0003_photo_private_original_expand"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY picflow_photo_uploaded_by_idx "
                        "ON picflow_photo (uploaded_by_id);"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS picflow_photo_uploaded_by_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE UNIQUE INDEX CONCURRENTLY picflow_photo_original_key_uniq "
                        "ON picflow_photo (original_key);"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS picflow_photo_original_key_uniq;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        f"{BEGIN_LOCK_TIMEOUT} "
                        "ALTER TABLE picflow_photo ADD CONSTRAINT "
                        "picflow_photo_original_key_uniq UNIQUE USING INDEX "
                        f"picflow_photo_original_key_uniq; {COMMIT}"
                    ),
                    reverse_sql=(
                        f"{BEGIN_LOCK_TIMEOUT} "
                        "ALTER TABLE picflow_photo DROP CONSTRAINT "
                        f"picflow_photo_original_key_uniq; {COMMIT}"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        f"{BEGIN_LOCK_TIMEOUT} "
                        "ALTER TABLE picflow_photo ADD CONSTRAINT "
                        "picflow_photo_uploaded_by_fk FOREIGN KEY (uploaded_by_id) "
                        "REFERENCES auth_user (id) DEFERRABLE INITIALLY DEFERRED NOT VALID; "
                        f"{COMMIT}"
                    ),
                    reverse_sql=(
                        f"{BEGIN_LOCK_TIMEOUT} "
                        "ALTER TABLE picflow_photo DROP CONSTRAINT "
                        f"picflow_photo_uploaded_by_fk; {COMMIT}"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        f"{BEGIN_LOCK_TIMEOUT} "
                        "ALTER TABLE picflow_photo ADD CONSTRAINT "
                        "picflow_photo_legacy_or_private_chk CHECK ("
                        "(src <> '' AND uploaded_by_id IS NULL AND original_key IS NULL "
                        "AND original_filename IS NULL AND original_size IS NULL "
                        "AND original_content_type IS NULL AND uploaded_at IS NULL) OR "
                        "(src = '' AND uploaded_by_id IS NOT NULL AND original_key IS NOT NULL "
                        "AND original_filename IS NOT NULL AND original_size IS NOT NULL "
                        "AND original_content_type IS NOT NULL AND uploaded_at IS NOT NULL)"
                        f") NOT VALID; {COMMIT}"
                    ),
                    reverse_sql=(
                        f"{BEGIN_LOCK_TIMEOUT} "
                        "ALTER TABLE picflow_photo DROP CONSTRAINT "
                        f"picflow_photo_legacy_or_private_chk; {COMMIT}"
                    ),
                ),
            ],
            state_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="photo",
                    name="original_key",
                    field=models.CharField(blank=True, max_length=255, null=True, unique=True),
                ),
                migrations.AlterField(
                    model_name="photo",
                    name="uploaded_by",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="uploaded_photos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AddIndex(
                    model_name="photo",
                    index=models.Index(
                        fields=["uploaded_by"], name="picflow_photo_uploaded_by_idx"
                    ),
                ),
                migrations.AddConstraint(
                    model_name="photo",
                    constraint=models.CheckConstraint(
                        condition=(
                            models.Q(
                                src__gt="",
                                uploaded_by__isnull=True,
                                original_key__isnull=True,
                                original_filename__isnull=True,
                                original_size__isnull=True,
                                original_content_type__isnull=True,
                                uploaded_at__isnull=True,
                            )
                            | models.Q(
                                src="",
                                uploaded_by__isnull=False,
                                original_key__isnull=False,
                                original_filename__isnull=False,
                                original_size__isnull=False,
                                original_content_type__isnull=False,
                                uploaded_at__isnull=False,
                            )
                        ),
                        name="picflow_photo_legacy_or_private_chk",
                    ),
                ),
            ],
        ),
    ]
