from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("picflow", "0014_photo_is_hidden")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="bib_search_enabled",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="photo",
            name="bib_processing_policy",
            field=models.CharField(
                choices=[("disabled", "Disabled"), ("original_v1", "Original image v1")],
                db_default="disabled",
                default="disabled",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="photo",
            constraint=models.CheckConstraint(
                condition=models.Q(("bib_processing_policy__in", ("disabled", "original_v1"))),
                name="picflow_photo_bib_policy_chk",
            ),
        ),
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION picflow_guard_bib_processing_policy_immutability()
                RETURNS trigger AS $$
                BEGIN
                    IF NEW.bib_processing_policy IS DISTINCT FROM OLD.bib_processing_policy THEN
                        RAISE EXCEPTION 'photo bib processing policy is immutable'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER picflow_bib_processing_policy_immutability_trg
                    BEFORE UPDATE OF bib_processing_policy ON picflow_photo
                    FOR EACH ROW
                    EXECUTE FUNCTION picflow_guard_bib_processing_policy_immutability();
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS picflow_bib_processing_policy_immutability_trg
                    ON picflow_photo;
                DROP FUNCTION IF EXISTS picflow_guard_bib_processing_policy_immutability();
            """,
        ),
    ]
