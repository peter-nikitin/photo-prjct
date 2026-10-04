"""Additive diagnostic schema preserves old jobs, lease clocks and published artifacts."""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = [pytest.mark.migration, pytest.mark.django_db(transaction=True)]


def test_telemetry_upgrade_preserves_terminal_retryable_active_expired_and_unenrolled_rows():
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    baseline = [("processing", "0012_worker_pool_coordination")]
    try:
        executor.migrate(baseline)
        apps = executor.loader.project_state(baseline).apps
        Event = apps.get_model("picflow", "Event")
        Photo = apps.get_model("picflow", "Photo")
        Run = apps.get_model("processing", "EventProcessingRun")
        Job = apps.get_model("processing", "ProcessingJob")
        Attempt = apps.get_model("processing", "ProcessingAttempt")
        now = timezone.now()
        event = Event.objects.create(
            name="Upgrade", slug="telemetry-upgrade", start_date=date.today(), end_date=date.today()
        )
        identity = {
            "event": event,
            "contract_version": 1,
            "processor_type": "face_embedding",
            "processor_version": 1,
            "configuration": {"preserved": True},
        }
        run = Run.objects.create(**identity, configuration_hash="a" * 64)
        cases = [
            ("succeeded", "succeeded", None),
            ("failed", "failed", None),
            ("retry_wait", "failed", None),
            ("cancelled", "stale", None),
            ("processing", "in_progress", now + timedelta(minutes=5)),
            ("processing", "in_progress", now - timedelta(minutes=5)),
            ("retry_wait", "expired", now - timedelta(minutes=10)),
        ]
        live_attempt = None
        for index, (job_status, attempt_status, expiry) in enumerate(cases):
            photo = Photo.objects.create(id=f"upgrade-{index}", event=event, src="old.jpg")
            job = Job.objects.create(
                **identity,
                run=run,
                photo=photo,
                configuration_hash="a" * 64,
                input_fingerprint={"preserved": index},
                status=job_status,
                available_at=now + timedelta(minutes=1),
            )
            attempt = Attempt.objects.create(
                **identity,
                run=run,
                job=job,
                photo=photo,
                input_fingerprint=job.input_fingerprint,
                status=attempt_status,
                worker_build="a" * 40,
                lease_expires_at=expiry,
                heartbeat_at=now,
                accepted=attempt_status == "succeeded",
                terminal_at=None if attempt_status == "in_progress" else now,
                result={"preserved": index},
                result_hash="b" * 64,
                error_code="old_failure" if attempt_status == "failed" else "",
            )
            if index == 4:
                live_attempt = attempt
            apps.get_model("processing", "PhotoProcessingState").objects.create(
                photo=photo,
                processor_type="face_embedding",
                status=job_status,
                current_run=run,
                current_job=job,
                current_attempt=attempt,
            )
            if attempt_status == "succeeded":
                apps.get_model("processing", "FaceProcessingAttemptArtifact").objects.create(
                    attempt=attempt,
                    feature_payload={"existing": True},
                    quality_payload={"existing": True},
                )
        Photo.objects.create(id="never-enrolled", event=event, src="untouched.jpg")
        pool = apps.get_model("processing", "WorkerPool").objects.create(
            name="bulk", group_id="group", active_build="a" * 40, staged_build="b" * 40
        )
        member = apps.get_model("processing", "WorkerPoolMember").objects.create(
            pool=pool,
            instance_id="old-instance",
            boot_id="12345678-1234-1234-1234-123456789012",
            worker_build="a" * 40,
            ready=True,
            active_processing_attempt=live_attempt,
        )
        names = [("picflow", "Photo")] + [
            ("processing", name)
            for name in (
                "EventProcessingRun",
                "ProcessingJob",
                "ProcessingAttempt",
                "PhotoProcessingState",
                "FaceProcessingAttemptArtifact",
                "WorkerPool",
                "WorkerPoolMember",
            )
        ]

        def inventory(registry):
            rows = {
                pair: list(registry.get_model(*pair).objects.order_by("pk").values())
                for pair in names
            }
            for row in rows[("processing", "ProcessingAttempt")]:
                row.pop("pool_member_id", None)
                row.pop("worker_build", None)
            for row in rows[("processing", "WorkerPoolMember")]:
                row.pop("active_processing_attempt_id", None)
            return rows

        before = inventory(apps)
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
        current = executor.loader.project_state(leaves).apps
        assert inventory(current) == before
        assert live_attempt is not None
        assert (
            current.get_model("processing", "ProcessingAttempt")
            .objects.get(pk=live_attempt.pk)
            .pool_member_id
            == member.pk
        )
        assert current.get_model("processing", "WorkerPoolTelemetry").objects.count() == 0
        assert (
            current.get_model("processing", "ProcessingJob")
            .objects.filter(photo_id="never-enrolled")
            .count()
            == 0
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)
