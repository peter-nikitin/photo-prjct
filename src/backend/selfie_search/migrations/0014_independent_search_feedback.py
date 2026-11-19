from django.db import migrations, models

TRANSFER_LABELS = """
    INSERT INTO search_feedback_searchfeedback (
        submission_id, event_id, photo_id, search_type, value,
        search_context, legacy_attachment_id, created_at
    )
    SELECT
        label.id, search.event_id, result.photo_id,
        CASE WHEN search.configuration->>'processor' = 'gallery_photo_query'
            THEN 'gallery' ELSE 'selfie' END,
        label.value,
        jsonb_build_object(
            'search_id', search.id,
            'result_id', result.id,
            'primary_source', result.primary_source
        ) || CASE WHEN search.configuration->>'processor' = 'gallery_photo_query'
            AND search.configuration #>> '{query_source,kind}' = 'gallery_photo'
            THEN jsonb_strip_nulls(jsonb_build_object(
                'source_photo_id', search.configuration #>> '{query_source,photo_id}',
                'source_detection_id', search.configuration #>> '{query_source,detection_id}'
            ))
            ELSE '{}'::jsonb END,
        attachment.id, attachment.created_at
    FROM selfie_search_selfiesearchfeedbacklabel AS label
    JOIN selfie_search_selfiesearchfeedback AS attachment ON attachment.id = label.feedback_id
    JOIN selfie_search_selfiesearchresult AS result ON result.id = label.result_id
    JOIN selfie_search_selfiesearch AS search ON search.id = result.search_id
"""

RESTORE_LABELS = """
    INSERT INTO selfie_search_selfiesearchfeedbacklabel (id, feedback_id, result_id, value)
    SELECT submission_id, legacy_attachment_id, (search_context->>'result_id')::uuid, value
    FROM search_feedback_searchfeedback
    WHERE legacy_attachment_id IS NOT NULL
"""


def migrate_feedback(apps, schema_editor):
    feedback = apps.get_model("selfie_search", "SelfieSearchFeedback")
    feedback.objects.using(schema_editor.connection.alias).filter(variant="result_labels").update(
        variant="selfie"
    )
    schema_editor.execute(TRANSFER_LABELS)


def restore_feedback(apps, schema_editor):
    feedback = apps.get_model("selfie_search", "SelfieSearchFeedback")
    feedback.objects.using(schema_editor.connection.alias).filter(variant="selfie").update(
        variant="result_labels"
    )
    schema_editor.execute(RESTORE_LABELS)
    schema_editor.execute(
        "DELETE FROM search_feedback_searchfeedback WHERE legacy_attachment_id IS NOT NULL"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("search_feedback", "0001_initial"),
        ("selfie_search", "0013_face_anchor_expansion"),
    ]

    operations = [
        migrations.RemoveConstraint("selfiesearchfeedback", "selfie_feedback_variant_chk"),
        migrations.RemoveConstraint("selfiesearchfeedback", "selfie_feedback_variant_source_chk"),
        migrations.RunPython(migrate_feedback, restore_feedback),
        migrations.DeleteModel("SelfieSearchFeedbackLabel"),
        migrations.AlterField(
            model_name="selfiesearchfeedback",
            name="variant",
            field=models.CharField(
                choices=[("problem", "Problem"), ("selfie", "Selfie after results")],
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            "selfiesearchfeedback",
            models.CheckConstraint(
                condition=models.Q(variant__in=("problem", "selfie")),
                name="selfie_feedback_variant_chk",
            ),
        ),
        migrations.AddConstraint(
            "selfiesearchfeedback",
            models.CheckConstraint(
                condition=(
                    models.Q(
                        variant="problem",
                        source_status__in=(
                            "no_face",
                            "multiple_faces",
                            "quality_rejected",
                            "search_unavailable",
                            "failed",
                        ),
                    )
                    | models.Q(
                        variant="problem",
                        source_status="ready",
                        source_visible_result_count=0,
                    )
                    | models.Q(
                        variant="selfie",
                        source_status="ready",
                        source_visible_result_count__gt=0,
                    )
                ),
                name="selfie_feedback_variant_source_chk",
            ),
        ),
    ]
