"""Synthetic previous-schema rehearsal for the additive bib-search schema."""

from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class BibSearchUpgradeTests(TransactionTestCase):
    def test_previous_processing_matrix_is_preserved_and_new_bib_rows_are_additive(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = [
            (
                app,
                {
                    "picflow": "0013_event_photo_price",
                    "processing": "0008_watermarked_preview_derivative_producer",
                }.get(app, name),
            )
            for app, name in latest
        ]
        executor.migrate(previous)
        try:
            old_apps = executor.loader.project_state(previous).apps
            Event = old_apps.get_model("picflow", "Event")
            Photo = old_apps.get_model("picflow", "Photo")
            Run = old_apps.get_model("processing", "EventProcessingRun")
            Job = old_apps.get_model("processing", "ProcessingJob")
            Attempt = old_apps.get_model("processing", "ProcessingAttempt")
            State = old_apps.get_model("processing", "PhotoProcessingState")
            Derivative = old_apps.get_model("processing", "PhotoDerivative")
            now = timezone.now()
            event = Event.objects.create(
                name="Previous bib schema",
                slug="previous-bib-schema",
                city="Moscow",
                start_date="2026-09-12",
                end_date="2026-09-12",
            )
            run = Run.objects.create(
                event=event,
                contract_version=2,
                processor_type="generate_preview",
                processor_version=1,
                configuration={},
                configuration_hash="a" * 64,
            )
            scenarios = (
                ("succeeded", "succeeded", "succeeded", True, 0),
                ("failed", "failed", "failed", False, 0),
                ("retryable", "retry_wait", "failed", False, 0),
                ("stale", "queued", "stale", False, 0),
                ("terminal", "cancelled", None, False, 0),
                ("active-lease", "processing", "in_progress", False, 120),
                ("expired-lease", "processing", "in_progress", False, -120),
            )
            photos = []
            for suffix, job_status, attempt_status, accepted, lease_offset in scenarios:
                photo = Photo.objects.create(
                    id=f"previous-{suffix}",
                    event=event,
                    src=f"photos/{suffix}.jpg",
                )
                photos.append(photo)
                job = Job.objects.create(
                    event=event,
                    run=run,
                    photo=photo,
                    contract_version=2,
                    processor_type="generate_preview",
                    processor_version=1,
                    configuration={},
                    configuration_hash="a" * 64,
                    input_fingerprint={},
                    status=job_status,
                    available_at=now + timedelta(minutes=1),
                    claimed_at=now if job_status == "processing" else None,
                    completed_at=now
                    if job_status in {"succeeded", "failed", "cancelled"}
                    else None,
                )
                attempt = None
                if attempt_status is not None:
                    attempt = Attempt.objects.create(
                        event=event,
                        run=run,
                        job=job,
                        photo=photo,
                        contract_version=2,
                        processor_type="generate_preview",
                        processor_version=1,
                        configuration={},
                        input_fingerprint={},
                        status=attempt_status,
                        claimed_at=now,
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=lease_offset),
                        terminal_at=None if attempt_status == "in_progress" else now,
                        accepted=accepted,
                    )
                State.objects.create(
                    photo=photo,
                    processor_type="generate_preview",
                    status=job_status,
                    current_run=run,
                    current_job=job,
                    current_attempt=attempt,
                    accepted_attempt=attempt if accepted else None,
                    next_attempt_at=(
                        now + timedelta(minutes=1) if job_status == "retry_wait" else None
                    ),
                )
                if accepted:
                    Derivative.objects.create(
                        photo=photo,
                        variant="preview-small-v1",
                        final_key=f"derivatives/{suffix}.jpg",
                        byte_size=80,
                        content_type="image/jpeg",
                        width=12,
                        height=8,
                        oriented_source_width=12,
                        oriented_source_height=8,
                        sha256="b" * 64,
                        accepted_attempt=attempt,
                    )

            never_enrolled = Photo.objects.create(
                id="previous-never-enrolled",
                event=event,
                src="photos/never-enrolled.jpg",
            )
            tracked_models = (Event, Photo, Run, Job, Attempt, State, Derivative)
            old_field_names = {
                model._meta.label: tuple(field.attname for field in model._meta.concrete_fields)
                for model in tracked_models
            }
            before = {
                model._meta.label: list(
                    model.objects.order_by("pk").values(*old_field_names[model._meta.label])
                )
                for model in tracked_models
            }

            MigrationExecutor(connection).migrate(latest)
            migrated_apps = MigrationExecutor(connection).loader.project_state(latest).apps
            for label, rows in before.items():
                self.assertEqual(
                    list(
                        migrated_apps.get_model(label)
                        .objects.order_by("pk")
                        .values(*old_field_names[label])
                    ),
                    rows,
                    label,
                )

            MigratedEvent = migrated_apps.get_model("picflow", "Event")
            MigratedPhoto = migrated_apps.get_model("picflow", "Photo")
            MigratedRun = migrated_apps.get_model("processing", "EventProcessingRun")
            MigratedJob = migrated_apps.get_model("processing", "ProcessingJob")
            MigratedAttempt = migrated_apps.get_model("processing", "ProcessingAttempt")
            MigratedState = migrated_apps.get_model("processing", "PhotoProcessingState")
            BibReading = migrated_apps.get_model("processing", "BibReading")
            self.assertFalse(MigratedEvent.objects.get(pk=event.pk).bib_search_enabled)
            self.assertFalse(
                MigratedPhoto.objects.exclude(bib_processing_policy="disabled").exists()
            )
            self.assertFalse(MigratedState.objects.filter(photo_id=never_enrolled.pk).exists())
            self.assertEqual(BibReading.objects.count(), 0)

            bib_photo = MigratedPhoto.objects.get(pk=photos[0].pk)
            bib_run = MigratedRun.objects.create(
                event_id=event.pk,
                contract_version=1,
                processor_type="bib_recognition",
                processor_version=1,
                configuration={},
                configuration_hash="c" * 64,
            )
            bib_job = MigratedJob.objects.create(
                event_id=event.pk,
                run=bib_run,
                photo=bib_photo,
                contract_version=1,
                processor_type="bib_recognition",
                processor_version=1,
                configuration={},
                configuration_hash="c" * 64,
                input_fingerprint={},
                status="succeeded",
                completed_at=now,
            )
            bib_attempt = MigratedAttempt.objects.create(
                event_id=event.pk,
                run=bib_run,
                job=bib_job,
                photo=bib_photo,
                contract_version=1,
                processor_type="bib_recognition",
                processor_version=1,
                configuration={},
                input_fingerprint={},
                status="succeeded",
                terminal_at=now,
                accepted=True,
            )
            MigratedState.objects.create(
                photo=bib_photo,
                processor_type="bib_recognition",
                status="succeeded",
                current_run=bib_run,
                current_job=bib_job,
                current_attempt=bib_attempt,
                accepted_attempt=bib_attempt,
                succeeded_at=now,
            )
            BibReading.objects.create(
                photo=bib_photo,
                source_attempt=bib_attempt,
                number="007",
                evidence={"decision": "accepted"},
            )

            self.assertEqual(BibReading.objects.get(photo=bib_photo).number, "007")
            old_app_photo = Photo.objects.get(pk=bib_photo.pk)
            self.assertEqual(old_app_photo.pk, bib_photo.pk)
            self.assertEqual(old_app_photo.event_id, event.pk)
            self.assertEqual(old_app_photo.src.name, "photos/succeeded.jpg")
            self.assertEqual(Attempt.objects.filter(photo_id=bib_photo.pk).count(), 2)
        finally:
            MigrationExecutor(connection).migrate(latest)
