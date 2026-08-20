from django.db import migrations

FORWARD_SQL = """
CREATE OR REPLACE FUNCTION proc_guard_photo_derivative_immutability() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM processing_processingattempt AS attempt
            WHERE attempt.id = NEW.accepted_attempt_id
              AND attempt.photo_id = NEW.photo_id
              AND (
                  (
                      NEW.variant = 'preview-small-v1'
                      AND attempt.processor_type = 'generate_preview'
                  )
                  OR (
                      NEW.variant = 'preview-watermarked-v1'
                      AND attempt.processor_type = 'generate_watermarked_preview'
                  )
              )
              AND attempt.status = 'succeeded'
              AND attempt.accepted IS TRUE
        ) THEN
            RAISE EXCEPTION
                'derivatives require the matching accepted successful producer attempt'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'published photo derivatives are immutable'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
"""


REVERSE_SQL = """
CREATE OR REPLACE FUNCTION proc_guard_photo_derivative_immutability() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM processing_processingattempt AS attempt
            WHERE attempt.id = NEW.accepted_attempt_id
              AND attempt.photo_id = NEW.photo_id
              AND attempt.processor_type = 'generate_preview'
              AND attempt.status = 'succeeded'
              AND attempt.accepted IS TRUE
        ) THEN
            RAISE EXCEPTION
                'derivatives require an accepted successful preview attempt'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'published photo derivatives are immutable'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("picflow", "0012_paid_watermarked_photo_policy"),
        ("processing", "0007_face_quality_generation"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
