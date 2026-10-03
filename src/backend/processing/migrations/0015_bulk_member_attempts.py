import django.db.models.deletion
from django.db import migrations, models


def transfer_active_attempts(apps, schema_editor):
    member_model = apps.get_model("processing", "WorkerPoolMember")
    attempt_model = apps.get_model("processing", "ProcessingAttempt")
    for member in member_model.objects.exclude(active_processing_attempt_id=None).iterator():
        attempt_model.objects.filter(pk=member.active_processing_attempt_id).update(
            pool_member_id=member.pk
        )


class Migration(migrations.Migration):
    dependencies = [("processing", "0014_bibreadingchange")]

    operations = [
        migrations.AddField(
            model_name="processingattempt",
            name="pool_member",
            field=models.ForeignKey(
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="processing_attempts",
                to="processing.workerpoolmember",
            ),
        ),
        migrations.RunPython(transfer_active_attempts, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="workerpoolmember", name="worker_member_one_attempt_chk"
        ),
        migrations.RemoveField(model_name="workerpoolmember", name="active_processing_attempt"),
    ]
