import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def migrate_feedback(apps, schema_editor):
    feedback = apps.get_model("selfie_search", "SelfieSearchFeedback")
    labels = apps.get_model("selfie_search", "SearchResultFeedback")
    database = schema_editor.connection.alias
    submissions = feedback.objects.using(database)
    submissions.filter(variant="result_labels").update(variant="selfie")
    labels.objects.using(database).update(
        created_at=Subquery(submissions.filter(pk=OuterRef("feedback_id")).values("created_at")[:1])
    )


def restore_feedback_variant(apps, schema_editor):
    feedback = apps.get_model("selfie_search", "SelfieSearchFeedback")
    feedback.objects.using(schema_editor.connection.alias).filter(variant="selfie").update(
        variant="result_labels"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("picflow", "0018_photo_gallery_order_index"),
        ("selfie_search", "0013_face_anchor_expansion"),
    ]

    operations = [
        migrations.RenameModel("SelfieSearchFeedbackLabel", "SearchResultFeedback"),
        migrations.RemoveConstraint(
            "searchresultfeedback", "selfie_feedback_label_membership_uniq"
        ),
        migrations.RemoveConstraint("searchresultfeedback", "selfie_feedback_label_value_chk"),
        migrations.RemoveConstraint("selfiesearchfeedback", "selfie_feedback_variant_chk"),
        migrations.RemoveConstraint("selfiesearchfeedback", "selfie_feedback_variant_source_chk"),
        migrations.AddField(
            model_name="searchresultfeedback",
            name="created_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(migrate_feedback, restore_feedback_variant),
        migrations.AlterField(
            model_name="searchresultfeedback",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name="searchresultfeedback",
            name="feedback",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="result_feedback",
                to="selfie_search.selfiesearchfeedback",
            ),
        ),
        migrations.AlterField(
            model_name="searchresultfeedback",
            name="result",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="result_feedback",
                to="selfie_search.selfiesearchresult",
            ),
        ),
        migrations.AddConstraint(
            "searchresultfeedback",
            models.UniqueConstraint(
                fields=("result", "value"), name="search_result_feedback_value_uniq"
            ),
        ),
        migrations.AddConstraint(
            "searchresultfeedback",
            models.CheckConstraint(
                condition=models.Q(value__in=("present", "absent")),
                name="search_result_feedback_value_chk",
            ),
        ),
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
        migrations.CreateModel(
            name="BibResultFeedback",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("bib_number", models.CharField(max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="picflow.event"
                    ),
                ),
                (
                    "photo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="picflow.photo"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("event", "bib_number", "photo"),
                        name="bib_result_feedback_membership_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(bib_number__regex=r"^[0-9]{1,16}$"),
                        name="bib_result_feedback_number_chk",
                    ),
                ],
            },
        ),
    ]
