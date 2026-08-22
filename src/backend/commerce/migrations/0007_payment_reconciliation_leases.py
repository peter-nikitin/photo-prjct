from datetime import timedelta

from django.db import migrations, models


def populate_reconciliation_schedule(apps, schema_editor):
    del schema_editor
    PaymentAttempt = apps.get_model("commerce", "PaymentAttempt")
    pending_attempts = PaymentAttempt.objects.filter(status="pending").iterator()
    for attempt in pending_attempts:
        due_at = attempt.expires_at or attempt.created_at + timedelta(hours=24)
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            reconciliation_state="pending",
            reconciliation_lease_id=None,
            reconciliation_lease_expires_at=None,
            reconciliation_next_attempt_at=due_at,
        )
    PaymentAttempt.objects.exclude(status="pending").update(
        reconciliation_state="pending",
        reconciliation_lease_id=None,
        reconciliation_lease_expires_at=None,
        reconciliation_next_attempt_at=None,
    )


class Migration(migrations.Migration):
    dependencies = [("commerce", "0006_paid_original_identity")]

    operations = [
        migrations.AddField(
            model_name="paymentattempt",
            name="reconciliation_state",
            field=models.CharField(
                choices=[("pending", "Pending"), ("processing", "Processing")],
                default="pending",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="reconciliation_lease_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="reconciliation_lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentattempt",
            name="reconciliation_next_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(populate_reconciliation_schedule, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("reconciliation_lease_expires_at__isnull", False),
                        ("reconciliation_lease_id__isnull", False),
                        ("reconciliation_state", "processing"),
                        ("status", "pending"),
                    )
                    | models.Q(
                        ("reconciliation_lease_expires_at__isnull", True),
                        ("reconciliation_lease_id__isnull", True),
                        ("reconciliation_state", "pending"),
                    )
                ),
                name="commerce_payment_reconcile_lease_chk",
            ),
        ),
        migrations.AddIndex(
            model_name="paymentattempt",
            index=models.Index(
                fields=[
                    "adapter_key",
                    "status",
                    "reconciliation_state",
                    "reconciliation_next_attempt_at",
                ],
                name="commerce_payment_reconcile_due_idx",
            ),
        ),
    ]
