from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("picflow", "0011_event_publication_states")]

    operations = [
        migrations.AlterField(
            model_name="photo",
            name="processing_generation",
            field=models.CharField(
                choices=[
                    ("legacy_original_v1", "Legacy original v1"),
                    ("preview_first_v1", "Preview first v1"),
                    ("preview_first_watermarked_v1", "Preview first watermarked v1"),
                ],
                db_default="legacy_original_v1",
                default="legacy_original_v1",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="photo",
            name="gallery_media_policy",
            field=models.CharField(
                choices=[
                    ("legacy_original_allowed", "Legacy original allowed"),
                    ("preview_required", "Preview required"),
                    ("watermarked_preview_required", "Watermarked preview required"),
                ],
                db_default="legacy_original_allowed",
                default="legacy_original_allowed",
                max_length=32,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="photo",
            name="picflow_photo_processing_policy_pair_chk",
        ),
        migrations.AddConstraint(
            model_name="photo",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        processing_generation="legacy_original_v1",
                        gallery_media_policy="legacy_original_allowed",
                    )
                    | models.Q(
                        processing_generation="preview_first_v1",
                        gallery_media_policy="preview_required",
                    )
                    | models.Q(
                        processing_generation="preview_first_watermarked_v1",
                        gallery_media_policy="watermarked_preview_required",
                    )
                ),
                name="picflow_photo_processing_policy_pair_chk",
            ),
        ),
    ]
