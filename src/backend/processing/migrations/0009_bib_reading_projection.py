import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

import processing.models


class Migration(migrations.Migration):
    dependencies = [
        ("picflow", "0015_bib_search_policy"),
        ("processing", "0008_watermarked_preview_derivative_producer"),
    ]

    operations = [
        migrations.CreateModel(
            name="BibReading",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "number",
                    models.CharField(
                        max_length=16,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Bib number must contain 1 to 16 ASCII digits.",
                                regex="\\A[0-9]{1,16}\\Z",
                            )
                        ],
                    ),
                ),
                (
                    "evidence",
                    models.JSONField(
                        default=dict,
                        validators=[processing.models.validate_bounded_json],
                    ),
                ),
                ("published_at", models.DateTimeField(auto_now_add=True)),
                (
                    "photo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bib_readings",
                        to="picflow.photo",
                    ),
                ),
                (
                    "source_attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bib_readings",
                        to="processing.processingattempt",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("number__regex", "^[0-9]{1,16}$")),
                        name="proc_bib_reading_number_chk",
                    ),
                    models.UniqueConstraint(
                        fields=("photo", "number"),
                        name="proc_bib_photo_number_uniq",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["number", "photo"],
                        name="proc_bib_number_photo_idx",
                    )
                ],
            },
        ),
    ]
