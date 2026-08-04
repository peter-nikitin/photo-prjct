import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import selfie_search.models


class Migration(migrations.Migration):
    dependencies = [
        ("selfie_search", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SelfieSearchFeedback",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "variant",
                    models.CharField(
                        choices=[("problem", "Problem"), ("result_labels", "Result labels")],
                        max_length=16,
                    ),
                ),
                ("contact", models.CharField(max_length=254)),
                ("personal_data_consent", models.BooleanField(default=False)),
                ("consent_text_version", models.CharField(max_length=32)),
                ("consented_at", models.DateTimeField()),
                (
                    "source_status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("processing", "Processing"),
                            ("cleanup_pending", "Cleanup pending"),
                            ("ready", "Ready"),
                            ("no_face", "No face"),
                            ("multiple_faces", "Multiple faces"),
                            ("quality_rejected", "Quality rejected"),
                            ("search_unavailable", "Search unavailable"),
                            ("failed", "Failed"),
                        ],
                        max_length=24,
                    ),
                ),
                ("source_matched_photo_count", models.PositiveIntegerField(default=0)),
                ("source_visible_result_count", models.PositiveIntegerField(default=0)),
                (
                    "source_configuration",
                    models.JSONField(
                        default=dict, validators=[selfie_search.models.validate_bounded_json]
                    ),
                ),
                ("object_key", models.CharField(max_length=255, unique=True)),
                ("object_content_type", models.CharField(max_length=100)),
                ("object_size", models.PositiveBigIntegerField()),
                ("object_uploaded_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "search",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="feedback",
                        to="selfie_search.selfiesearch",
                    ),
                ),
            ],
            options={
                "permissions": [
                    ("view_sensitive_feedback", "Can view sensitive selfie search feedback")
                ],
            },
        ),
        migrations.CreateModel(
            name="SelfieSearchFeedbackAccessAudit",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[("contact_view", "Contact view"), ("selfie_view", "Selfie view")],
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "feedback",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="access_audits",
                        to="selfie_search.selfiesearchfeedback",
                    ),
                ),
                (
                    "staff",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="selfie_search_feedback_access_audits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SelfieSearchFeedbackLabel",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "value",
                    models.CharField(
                        choices=[("present", "Present"), ("absent", "Absent")], max_length=7
                    ),
                ),
                (
                    "feedback",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="labels",
                        to="selfie_search.selfiesearchfeedback",
                    ),
                ),
                (
                    "result",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="feedback_labels",
                        to="selfie_search.selfiesearchresult",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(("variant__in", ("problem", "result_labels"))),
                name="selfie_feedback_variant_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(("personal_data_consent", True)),
                name="selfie_feedback_consent_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "source_status__in",
                        (
                            "ready",
                            "no_face",
                            "multiple_faces",
                            "quality_rejected",
                            "search_unavailable",
                            "failed",
                        ),
                    )
                ),
                name="selfie_feedback_source_status_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        (
                            "source_status__in",
                            (
                                "no_face",
                                "multiple_faces",
                                "quality_rejected",
                                "search_unavailable",
                                "failed",
                            ),
                        ),
                        ("variant", "problem"),
                    ),
                    models.Q(
                        ("source_status", "ready"),
                        ("source_visible_result_count", 0),
                        ("variant", "problem"),
                    ),
                    models.Q(
                        ("source_status", "ready"),
                        ("source_visible_result_count__gt", 0),
                        ("variant", "result_labels"),
                    ),
                    _connector="OR",
                ),
                name="selfie_feedback_variant_source_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(("contact", ""), _negated=True),
                name="selfie_feedback_contact_nonempty_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(("object_content_type__in", ("image/jpeg", "image/png"))),
                name="selfie_feedback_object_type_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedback",
            constraint=models.CheckConstraint(
                condition=models.Q(("object_size__gt", 0), ("object_size__lte", 20971520)),
                name="selfie_feedback_object_size_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedbackaccessaudit",
            constraint=models.CheckConstraint(
                condition=models.Q(("action__in", ("contact_view", "selfie_view"))),
                name="selfie_feedback_audit_action_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedbacklabel",
            constraint=models.UniqueConstraint(
                fields=("feedback", "result"), name="selfie_feedback_label_membership_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchfeedbacklabel",
            constraint=models.CheckConstraint(
                condition=models.Q(("value__in", ("present", "absent"))),
                name="selfie_feedback_label_value_chk",
            ),
        ),
    ]
