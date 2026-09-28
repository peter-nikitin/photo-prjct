import json
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import UUID, uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from processing.models import ProcessingAttempt, WorkerPool
from processing.tests.test_worker_pool_state_command import WorkerPoolStateCommandTests


@override_settings(PHOTO_PROCESSING_FLEET_TOKEN="fleet-token")
class WorkerPoolMetricsTests(WorkerPoolStateCommandTests):
    def metrics(self, **options):
        output = StringIO()
        call_command("publish_worker_pool_metrics", zone="ru-central1-a", stdout=output, **options)
        return json.loads(output.getvalue())

    def test_empty_queue_publishes_explicit_zero_for_each_pool_without_network_or_writes(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")):
            metrics = self.metrics()["metrics"]
        demand = {
            m["labels"]["pool"]: m["value"] for m in metrics if m["name"] == "worker_pool_workload"
        }
        self.assertEqual(demand, {"bulk": 0, "selfie": 0})
        self.assertTrue(all(set(m["labels"]) == {"pool", "zone_id"} for m in metrics))

    def test_workload_counts_only_due_candidates_and_current_leases_in_both_stores(self):
        self.bulk_job()
        self.bulk_job("retry_wait", identity=(2, "generate_preview", 1))
        self.bulk_job("retry_wait", due=False, identity=(2, "generate_watermarked_preview", 1))
        self.bulk_attempt(self.bulk_job("processing", identity=(2, "face_embedding", 3)))
        from picflow.models import Photo

        other = Photo.objects.create(
            id="second", event=self.event, src="https://private-url/second.jpg"
        )
        expired = self.bulk_attempt(
            self.bulk_job("processing", identity=(3, "face_embedding", 5), photo=other),
            expired=True,
        )
        self.bulk_attempt(
            self.bulk_job("processing", identity=(1, "bib_recognition", 1), current=False),
            expired=True,
        )
        self.selfie_job()
        self.selfie_job("retry_wait")
        self.selfie_job("retry_wait", due=False)
        from selfie_search.models import SelfieSearchAttempt

        SelfieSearchAttempt.objects.create(
            job=self.selfie_job("processing", search_status="processing"),
            lease_expires_at=self.now - timedelta(seconds=1),
        )
        SelfieSearchAttempt.objects.create(
            job=self.selfie_job("processing", search_status="processing"),
            lease_expires_at=self.now + timedelta(hours=1),
        )
        metrics = self.metrics()["metrics"]
        demand = {
            m["labels"]["pool"]: m["value"] for m in metrics if m["name"] == "worker_pool_workload"
        }
        self.assertEqual(demand, {"bulk": 4, "selfie": 4})
        expired.refresh_from_db()
        self.assertEqual(expired.status, ProcessingAttempt.Status.IN_PROGRESS)

    @override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_capacity_uses_only_current_complete_running_membership_not_target_or_history(self):
        from django.utils import timezone

        from processing.services import worker_pool_lifecycle as lifecycle

        now = timezone.now()
        for name in ("bulk", "selfie"):
            lifecycle.configure_pool(name, group_id=f"{name}-group", active_build="a" * 40)
            lifecycle.record_cloud_snapshot(
                name,
                group_id=f"{name}-group",
                sequence=1,
                started_at=now,
                completed_at=now,
                target_size=2,
                complete=True,
                members=[
                    {
                        "instance_id": f"{name}-old",
                        "status": "RUNNING_OUTDATED",
                        "worker_build": "a" * 40,
                    },
                    {
                        "instance_id": f"{name}-current",
                        "status": "RUNNING_ACTUAL",
                        "worker_build": "a" * 40,
                    },
                ],
            )
        result = self.metrics()["metrics"]
        capacity = {
            m["labels"]["pool"]: m["value"]
            for m in result
            if m["name"] == "worker_pool_running_instances"
        }
        self.assertEqual(capacity, {"bulk": 2, "selfie": 2})
        later = timezone.now()
        lifecycle.record_cloud_snapshot(
            "bulk",
            group_id="bulk-group",
            sequence=2,
            started_at=later,
            completed_at=later,
            target_size=2,
            complete=True,
            members=[
                {
                    "instance_id": "bulk-current",
                    "status": "RUNNING_ACTUAL",
                    "worker_build": "a" * 40,
                },
                {"instance_id": "", "status": "CREATING_INSTANCE", "worker_build": ""},
            ],
        )
        lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="selfie-group",
            sequence=2,
            started_at=later,
            completed_at=later,
            target_size=2,
            complete=True,
            members=[],
        )
        result = self.metrics()["metrics"]
        capacity = {
            m["labels"]["pool"]: m["value"]
            for m in result
            if m["name"] == "worker_pool_running_instances"
        }
        self.assertEqual(capacity, {"bulk": 1, "selfie": 0})

    @override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_missing_stale_or_partial_capacity_never_suppresses_demand_or_invents_zero(self):
        from django.utils import timezone

        from processing.services import worker_pool_lifecycle as lifecycle

        self.bulk_job()
        now = timezone.now()
        for name in ("bulk", "selfie"):
            lifecycle.configure_pool(name, group_id=f"{name}-group", active_build="a" * 40)
        for kind in ("missing", "stale", "partial"):
            if kind != "missing":
                for name in ("bulk", "selfie"):
                    lifecycle.record_cloud_snapshot(
                        name,
                        group_id=f"{name}-group",
                        sequence=2 if kind == "partial" else 1,
                        started_at=now,
                        completed_at=now,
                        target_size=2,
                        members=[],
                        complete=kind != "partial",
                    )
            with (
                patch(
                    "django.utils.timezone.now",
                    return_value=now
                    + timedelta(seconds={"missing": 0, "stale": 91, "partial": 92}[kind]),
                ),
                patch("processing.services.worker_pool_metrics.write_metrics"),
            ):
                result = self.metrics(publish=True, folder_id="folder")
            self.assertTrue(result["published"])
            values = {
                m["name"]: m["value"] for m in result["metrics"] if m["labels"]["pool"] == "bulk"
            }
            self.assertEqual(values["worker_pool_workload"], 1)
            self.assertEqual(values["worker_pool_capacity_fresh"], 0)
            self.assertNotIn("worker_pool_running_instances", values)

    @override_settings(PHOTO_PROCESSING_ENABLED=False)
    def test_endpoint_fault_is_not_queue_zero(self):
        self.bulk_job()
        with self.assertRaises(CommandError):
            self.metrics()

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=False)
    def test_disabled_enrollment_still_counts_durable_jobs(self):
        self.bulk_job(identity=(2, "face_embedding", 3))
        metrics = self.metrics()["metrics"]
        self.assertEqual(
            next(
                m["value"]
                for m in metrics
                if m["name"] == "worker_pool_workload" and m["labels"]["pool"] == "bulk"
            ),
            1,
        )

    def test_missing_expiry_is_a_fault_instead_of_silently_stranding_work(self):
        attempt = self.bulk_attempt(self.bulk_job("processing"))
        ProcessingAttempt.objects.filter(pk=attempt.pk).update(lease_expires_at=None)
        with self.assertRaises(CommandError):
            self.metrics()

    @override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_failed_publication_does_not_refresh_queue_or_emit_success(self):
        from processing.services.worker_pool_lifecycle import configure_pool

        configure_pool("bulk", group_id="bulk-group", active_build="a" * 40)
        configure_pool("selfie", group_id="selfie-group", active_build="a" * 40)
        with patch(
            "processing.services.worker_pool_metrics.write_metrics",
            side_effect=ValueError("secret transport error"),
        ):
            with self.assertRaisesMessage(CommandError, "worker pool publication failed"):
                self.metrics(publish=True, folder_id="folder")
        self.assertTrue(all(pool.queue_observed_at is None for pool in WorkerPool.objects.all()))

    @override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_successful_complete_write_updates_queue_freshness_after_write(self):
        from processing.services.worker_pool_lifecycle import configure_pool

        for name in ("bulk", "selfie"):
            configure_pool(name, group_id=f"{name}-group", active_build="a" * 40)
        observed = []

        def writer(*args):
            observed.append(
                all(pool.queue_observed_at is None for pool in WorkerPool.objects.all())
            )

        with patch("processing.services.worker_pool_metrics.write_metrics", side_effect=writer):
            result = self.metrics(publish=True, folder_id="folder")
        self.assertEqual(observed, [True])
        self.assertTrue(result["published"])
        self.assertTrue(
            all(pool.queue_observed_at is not None for pool in WorkerPool.objects.all())
        )

    @override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
    def test_preempted_bulk_at_zero_retains_demand_until_woken_claim_recovers_lease(self):
        from django.utils import timezone

        from processing.services import worker_pool_lifecycle as lifecycle

        job = self.bulk_job("processing")
        expired = self.bulk_attempt(job, expired=True)
        pool = lifecycle.configure_pool("bulk", group_id="bulk-group", active_build="a" * 40)
        self.assertEqual(pool.target_size, 0)
        metrics = self.metrics()["metrics"]
        self.assertEqual(
            next(
                m["value"]
                for m in metrics
                if m["name"] == "worker_pool_workload" and m["labels"]["pool"] == "bulk"
            ),
            1,
        )
        expired.refresh_from_db()
        self.assertEqual(expired.status, "in_progress")
        now = timezone.now()
        lifecycle.record_cloud_snapshot(
            "bulk",
            group_id="bulk-group",
            sequence=1,
            started_at=now,
            completed_at=now,
            target_size=1,
            members=[
                {
                    "instance_id": "new-capacity",
                    "status": "RUNNING_ACTUAL",
                    "worker_build": "a" * 40,
                }
            ],
            complete=True,
        )
        lifecycle.set_claims_paused("bulk", paused=False)
        member = lifecycle.MemberIdentity("bulk", "new-capacity", uuid4(), "a" * 40)
        registration = lifecycle.register(member)
        member = replace(
            member, registration_generation=UUID(str(registration["registration_generation"]))
        )
        lifecycle.heartbeat(member, ready=True, draining=False)
        response = self.client.post(
            "/internal/photo-processing/v1/claim",
            {
                "contract_version": 1,
                "processor_type": "capture_metadata",
                "processor_version": 2,
                "worker_build": "a" * 40,
                "lease_seconds": 120,
                "pool": "bulk",
                "instance_id": member.instance_id,
                "boot_id": str(member.boot_id),
                "registration_generation": str(member.registration_generation),
            },
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer fleet-token",
            HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
        )
        self.assertEqual(response.status_code, 200, response.content)
        expired.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(expired.status, "expired")
        self.assertEqual(job.status, "retry_wait")
        self.assertTrue(response.json()["empty"])
