"""Synthetic previous-schema state matrix: additive upgrade, no reset or backfill."""

from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ImportUpgradeTests(TransactionTestCase):
    def test_previous_schema_matrix_is_preserved_exactly(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        # Product 899e9cc has exactly this ingestion leaf; all other app leaves are
        # unchanged by this feature. Only the six new import tables are absent.
        previous = [
            (app, name if app != "ingestion" else "0003_uploaditem_folder") for app, name in latest
        ]
        executor.migrate(previous)
        try:
            apps = executor.loader.project_state(previous).apps
            User = apps.get_model("auth", "User")
            Event = apps.get_model("picflow", "Event")
            Photo = apps.get_model("picflow", "Photo")
            Batch = apps.get_model("ingestion", "UploadBatch")
            Item = apps.get_model("ingestion", "UploadItem")
            Run = apps.get_model("processing", "EventProcessingRun")
            Job = apps.get_model("processing", "ProcessingJob")
            Attempt = apps.get_model("processing", "ProcessingAttempt")
            State = apps.get_model("processing", "PhotoProcessingState")
            Derivative = apps.get_model("processing", "PhotoDerivative")
            now = timezone.now()
            owner = User.objects.create(username="previous-schema")
            event = Event.objects.create(
                name="Previous",
                slug="previous",
                city="Moscow",
                start_date="2026-09-07",
                end_date="2026-09-07",
            )
            batch = Batch.objects.create(event=event, uploader=owner, expected_item_count=8)
            for index, status in enumerate(
                [
                    "succeeded",
                    "failed",
                    "expired",
                    "stale",
                    "in_progress",
                    "in_progress",
                    "queued",
                    "never-enrolled",
                ]
            ):
                photo = Photo.objects.create(
                    id=f"previous-{index}",
                    event=event,
                    src="",
                    uploaded_by=owner,
                    original_key=f"originals/{uuid4().hex}",
                    original_filename=f"{index}.jpg",
                    original_size=100,
                    original_content_type="image/jpeg",
                    uploaded_at=now,
                )
                Item.objects.create(
                    batch=batch,
                    client_item_id=uuid4(),
                    original_filename=f"{index}.jpg",
                    declared_content_type="image/jpeg",
                    expected_size=100,
                    incoming_key=f"incoming/{batch.pk}/{uuid4()}",
                    final_key=photo.original_key,
                    photo=photo,
                    status="uploaded",
                )
                if status == "never-enrolled":
                    continue
                run = Run.objects.create(
                    event=event,
                    contract_version=2,
                    processor_type="generate_preview",
                    processor_version=1,
                    configuration_hash="a" * 64,
                )
                job_status = {
                    "expired": "retry_wait",
                    "stale": "failed",
                    "in_progress": "processing",
                }.get(status, status)
                job = Job.objects.create(
                    event=event,
                    run=run,
                    photo=photo,
                    contract_version=2,
                    processor_type="generate_preview",
                    processor_version=1,
                    configuration_hash="a" * 64,
                    status=job_status,
                )
                attempt = None
                if status != "queued":
                    attempt = Attempt.objects.create(
                        event=event,
                        run=run,
                        job=job,
                        photo=photo,
                        contract_version=2,
                        processor_type="generate_preview",
                        processor_version=1,
                        status=status,
                        claimed_at=now - timedelta(minutes=5),
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=120 if index == 4 else -120),
                        terminal_at=None if status == "in_progress" else now,
                        accepted=status == "succeeded",
                    )
                State.objects.create(
                    photo=photo,
                    processor_type="generate_preview",
                    status=job_status,
                    current_run=run,
                    current_job=job,
                    current_attempt=attempt,
                    accepted_attempt=attempt if status == "succeeded" else None,
                )
                if status == "succeeded":
                    Derivative.objects.create(
                        photo=photo,
                        variant="preview-small-v1",
                        final_key=f"derivatives/{uuid4().hex}",
                        byte_size=80,
                        content_type="image/jpeg",
                        width=12,
                        height=8,
                        oriented_source_width=12,
                        oriented_source_height=8,
                        sha256="b" * 64,
                        accepted_attempt=attempt,
                    )
            models = [Photo, Batch, Item, Run, Job, Attempt, State, Derivative]
            before = {
                model._meta.label: list(model.objects.order_by("pk").values()) for model in models
            }
            MigrationExecutor(connection).migrate(latest)
            after_apps = MigrationExecutor(connection).loader.project_state(latest).apps
            for label, rows in before.items():
                self.assertEqual(
                    list(after_apps.get_model(label).objects.order_by("pk").values()), rows, label
                )
            self.assertEqual(after_apps.get_model("ingestion", "ImportBatch").objects.count(), 0)
            self.assertFalse(State.objects.filter(photo_id="previous-7").exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
