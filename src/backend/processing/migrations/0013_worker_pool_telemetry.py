import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("processing", "0012_worker_pool_coordination")]

    operations = [
        migrations.CreateModel(
            name="WorkerPoolTelemetry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("instance_id", models.CharField(max_length=64)),
                ("sampled_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("envelope", models.JSONField()),
                ("runtime_baseline", models.JSONField(default=None, null=True)),
                ("container_baseline", models.JSONField(default=None, null=True)),
                ("runtime_reset_at", models.DateTimeField(default=None, null=True)),
                ("container_reset_at", models.DateTimeField(default=None, null=True)),
                (
                    "pool",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="telemetry",
                        to="processing.workerpool",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("pool", "instance_id"), name="worker_telemetry_source_uniq"
                    )
                ]
            },
        ),
    ]
