from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("picflow", "0005_validate_photo_private_original_constraints")]

    operations = [
        migrations.AddField(
            model_name="photo",
            name="processing_generation",
            field=models.CharField(
                choices=[
                    ("legacy_original_v1", "Legacy original v1"),
                    ("preview_first_v1", "Preview first v1"),
                ],
                db_default="legacy_original_v1",
                default="legacy_original_v1",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="photo",
            name="gallery_media_policy",
            field=models.CharField(
                choices=[
                    ("legacy_original_allowed", "Legacy original allowed"),
                    ("preview_required", "Preview required"),
                ],
                db_default="legacy_original_allowed",
                default="legacy_original_allowed",
                max_length=32,
            ),
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
                ),
                name="picflow_photo_processing_policy_pair_chk",
            ),
        ),
    ]
