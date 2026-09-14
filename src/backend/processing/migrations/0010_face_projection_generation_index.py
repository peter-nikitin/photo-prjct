from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("processing", "0009_bib_reading_projection"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="photofaceembeddingprojection",
            index=models.Index(
                fields=[
                    "contract_version",
                    "processor_version",
                    "configuration_hash",
                    "photo",
                    "accepted_attempt",
                ],
                name="proc_face_proj_gen_idx",
            ),
        ),
    ]
