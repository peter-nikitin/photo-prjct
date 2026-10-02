import copy
import json
import os
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo
from prometheus_client.parser import text_string_to_metric_families

from processing.contracts import ClaimedJob
from processing.models import WorkerPool, WorkerPoolMember
from processing.services import worker_pool_lifecycle as lifecycle

BUILD = "a" * 40
URL = "/internal/photo-processing/v1/members/telemetry"


@override_settings(
    PHOTO_PROCESSING_ENABLED=True,
    PHOTO_PROCESSING_WORKER_TOKEN="local",
    PHOTO_PROCESSING_FLEET_TOKEN="fleet",
    PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True,
)
class TelemetryTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.pool = lifecycle.configure_pool("selfie", group_id="group", active_build=BUILD)
        lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="group",
            sequence=1,
            started_at=self.now,
            completed_at=self.now,
            target_size=1,
            members=[{"instance_id": "node-1", "status": "RUNNING_ACTUAL", "worker_build": BUILD}],
            complete=True,
        )
        self.identity = lifecycle.MemberIdentity("selfie", "node-1", uuid4(), BUILD)
        registered = lifecycle.register(self.identity)
        self.pool.refresh_from_db()
        self.data = {
            "pool": "selfie",
            "instance_id": "node-1",
            "boot_id": str(self.identity.boot_id),
            "worker_build": BUILD,
            "zone_id": "ru-central1-a",
            "collector_epoch": str(uuid4()),
            "collector_started_at": (self.now - timedelta(seconds=10)).isoformat(),
            "sequence": 1,
            "sampled_at": self.now.isoformat(),
            "host": {
                "cpu_utilization": 0.5,
                "memory_available_bytes": 100,
                "memory_total_bytes": 200,
                "root_available_bytes": 300,
                "root_total_bytes": 400,
            },
            "container": {
                "present": True,
                "running": True,
                "container_id": "c" * 64,
                "restart_count": 1,
                "oom_killed": False,
                "exit_code": 0,
                "cpu_usage_cores": 0.4,
                "cpu_limit_cores": 2,
                "memory_usage_bytes": 50,
                "memory_limit_bytes": 200,
                "events_available": True,
                "events_since": self.now.isoformat(),
                "oom_events": 0,
                "restart_events": 1,
            },
            "runtime": {
                "registration_generation": registered["registration_generation"],
                "sampled_at": self.now.isoformat(),
                "busy": 0,
                "aggregates": {
                    "selfie_query": {"callback_delivered": [2, 6.0, [0, 1, 2, 2, 2, 2, 2, 2]]}
                },
            },
        }

    def submit(self, data=None, **headers):
        return self.client.post(
            URL,
            json.dumps(self.data if data is None else data),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer fleet",
            HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
            **headers,
        )

    def samples(self):
        response = self.client.get("/worker-diagnostics/metrics/")
        self.assertEqual(response.status_code, 200)
        return {
            sample.name: sample
            for family in text_string_to_metric_families(response.content.decode())
            for sample in family.samples
        }

    def all_samples(self):
        response = self.client.get("/worker-diagnostics/metrics/")
        self.assertEqual(response.status_code, 200)
        return [
            sample
            for family in text_string_to_metric_families(response.content.decode())
            for sample in family.samples
        ]

    def test_private_scrape_exposes_pool_sources_without_node_identifiers(self):
        published_at = self.now - timedelta(seconds=20)
        self.pool.queue_observed_at = published_at
        self.pool.save(update_fields=["queue_observed_at"])

        with patch("django.utils.timezone.now", return_value=self.now):
            samples = self.all_samples()

        pool_samples = [sample for sample in samples if sample.name.startswith("worker_pool_")]
        self.assertTrue(pool_samples)
        self.assertTrue(all(set(sample.labels) == {"pool"} for sample in pool_samples))
        by_name_and_pool = {(sample.name, sample.labels["pool"]): sample for sample in pool_samples}
        self.assertEqual(
            by_name_and_pool[("worker_pool_queue_observation_available", "bulk")].value,
            1,
        )
        self.assertEqual(
            by_name_and_pool[("worker_pool_queue_observation_timestamp_seconds", "selfie")].value,
            self.now.timestamp(),
        )
        self.assertEqual(
            by_name_and_pool[("worker_pool_cloud_observation_timestamp_seconds", "selfie")].value,
            self.now.timestamp(),
        )
        self.assertEqual(
            by_name_and_pool[
                ("worker_pool_native_publisher_success_timestamp_seconds", "selfie")
            ].value,
            published_at.timestamp(),
        )
        self.assertEqual(
            by_name_and_pool[("worker_pool_running_instances", "selfie")].value,
            1,
        )
        self.assertEqual(
            by_name_and_pool[("worker_pool_expected_instances", "selfie")].value,
            1,
        )

    def test_queue_observation_failure_keeps_node_diagnostics_and_reports_unavailable(self):
        self.assertEqual(self.submit().status_code, 200)

        with patch(
            "processing.services.worker_pool_telemetry.observe_pool_state",
            side_effect=ValueError("private queue detail"),
        ):
            samples = self.all_samples()

        self.assertTrue(any(sample.name == "worker_host_observation_fresh" for sample in samples))
        availability = {
            sample.labels["pool"]: sample.value
            for sample in samples
            if sample.name == "worker_pool_queue_observation_available"
        }
        self.assertEqual(availability, {"bulk": 0, "selfie": 0})

    def test_pending_slot_without_instance_identity_is_not_an_expected_node(self):
        lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="group",
            sequence=2,
            started_at=self.now,
            completed_at=self.now,
            target_size=1,
            members=[
                {
                    "instance_id": "",
                    "status": "STARTING_INSTANCE",
                    "worker_build": "",
                }
            ],
            complete=True,
        )

        with patch("django.utils.timezone.now", return_value=self.now):
            samples = self.all_samples()

        selfie = {
            sample.name: sample.value for sample in samples if sample.labels == {"pool": "selfie"}
        }
        self.assertEqual(selfie["worker_pool_running_instances"], 0)
        self.assertEqual(selfie["worker_pool_expected_instances"], 0)
        self.assertFalse(
            any(
                sample.name == "worker_host_observation_missing"
                and sample.labels["pool"] == "selfie"
                for sample in samples
            )
        )

    def test_cloud_commit_between_pool_and_node_export_uses_one_materialized_membership(self):
        from processing.services import worker_pool_telemetry as telemetry

        observe = telemetry.observe_pool_state
        clock = [self.now]

        def commit_after_pool_observation(*args, **kwargs):
            observation = observe(*args, **kwargs)
            clock[0] = self.now + timedelta(seconds=4)
            WorkerPool.objects.filter(name="selfie").update(
                observation_sequence=2,
                observation_completed_at=clock[0],
                observed_members=[
                    {"instance_id": "node-2", "status": "RUNNING_ACTUAL", "worker_build": BUILD}
                ],
            )
            return observation

        with (
            patch("django.utils.timezone.now", side_effect=lambda: clock[0]),
            patch.object(
                telemetry, "observe_pool_state", side_effect=commit_after_pool_observation
            ),
        ):
            samples = self.all_samples()
        pool_timestamp = next(
            sample.value
            for sample in samples
            if sample.name == "worker_pool_cloud_observation_timestamp_seconds"
            and sample.labels["pool"] == "selfie"
        )
        node_timestamps = [
            sample
            for sample in samples
            if sample.name == "worker_node_cloud_observation_timestamp_seconds"
        ]
        self.assertEqual(len(node_timestamps), 1)
        self.assertEqual(node_timestamps[0].value, pool_timestamp)
        self.assertEqual(node_timestamps[0].labels["instance_id"], "node-1")
        self.assertEqual(WorkerPool.objects.get(name="selfie").observation_sequence, 2)

    def test_unavailable_queue_does_not_suppress_independent_fresh_node_diagnostics(self):
        with patch("django.utils.timezone.now", return_value=self.now):
            self.assertEqual(self.submit().status_code, 200)
            with override_settings(PHOTO_PROCESSING_ENABLED=False):
                samples = self.samples()
        self.assertEqual(samples["worker_pool_queue_observation_available"].value, 0)
        self.assertNotIn("worker_pool_running_instances", samples)
        self.assertEqual(samples["worker_cloud_observation_fresh"].value, 1)
        self.assertEqual(samples["worker_host_observation_fresh"].value, 1)
        self.assertEqual(samples["worker_runtime_observation_fresh"].value, 1)

    def test_actually_future_cloud_row_never_exports_fresh_capacity_or_node_flags(self):
        with patch("django.utils.timezone.now", return_value=self.now):
            self.assertEqual(self.submit().status_code, 200)
        WorkerPool.objects.filter(name="selfie").update(
            observation_completed_at=self.now + timedelta(seconds=1)
        )
        with patch("django.utils.timezone.now", return_value=self.now):
            samples = self.samples()
        self.assertNotIn("worker_pool_running_instances", samples)
        self.assertNotIn("worker_pool_expected_instances", samples)
        self.assertEqual(samples["worker_cloud_observation_fresh"].value, 0)
        self.assertEqual(samples["worker_host_observation_fresh"].value, 0)
        self.assertEqual(samples["worker_runtime_observation_fresh"].value, 0)

    def test_receipt_is_separate_and_never_changes_admission_or_native_metrics(self):
        from config.metrics import generate_metrics

        from processing.models import WorkerPoolTelemetry
        from processing.services.worker_pool_metrics import observe_metrics

        member = list(WorkerPoolMember.objects.values())
        pool = list(WorkerPool.objects.values())
        before = observe_metrics("ru-central1-a")["metrics"]
        self.assertEqual(self.submit().json(), {"accepted": True, "duplicate": False})
        row = WorkerPoolTelemetry.objects.get()
        self.assertEqual(row.sampled_at, self.now)
        self.assertGreaterEqual(row.received_at, self.now)
        self.assertEqual(member, list(WorkerPoolMember.objects.values()))
        self.assertEqual(pool, list(WorkerPool.objects.values()))
        after = observe_metrics("ru-central1-a")["metrics"]
        self.assertEqual(
            [x for x in before if x["name"] != "worker_pool_observed_timestamp"],
            [x for x in after if x["name"] != "worker_pool_observed_timestamp"],
        )
        with (
            TemporaryDirectory() as metrics_dir,
            patch.dict(os.environ, PROMETHEUS_MULTIPROC_DIR=metrics_dir),
        ):
            self.assertNotIn(b"worker_host", generate_metrics())
        samples = self.samples()
        self.assertEqual(samples["worker_host_memory_available_bytes"].value, 100)
        self.assertEqual(samples["worker_runtime_executions_total"].value, 2)
        for sample in samples.values():
            self.assertLessEqual(
                set(sample.labels), {"pool", "instance_id", "zone_id", "kind", "outcome", "le"}
            )

    def test_local_and_wrong_credentials_cannot_submit(self):
        for marker, token in [("", "local"), ("private-tls", "local"), ("spoofed", "fleet")]:
            response = self.client.post(
                URL,
                self.data,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
                HTTP_X_FINDME_WORKER_TRANSPORT=marker,
            )
            self.assertEqual(response.status_code, 401)

    def test_invalid_diagnostic_envelopes_are_rejected(self):
        changes = [
            ("instance_id", "unknown"),
            ("worker_build", "b" * 40),
            ("boot_id", str(uuid4())),
            ("zone_id", "user-secret"),
            ("sampled_at", (self.now + timedelta(seconds=1)).isoformat()),
            ("sampled_at", (self.now - timedelta(seconds=91)).isoformat()),
            ("sequence", True),
            ("product_id", "secret"),
        ]
        for key, value in changes:
            data = copy.deepcopy(self.data)
            data[key] = value
            self.assertNotEqual(self.submit(data).status_code, 200, key)
        for layer, key, value in [
            ("host", "cpu_utilization", float("nan")),
            ("container", "customer", "secret"),
            ("runtime", "registration_generation", str(uuid4())),
        ]:
            data = copy.deepcopy(self.data)
            data[layer][key] = value
            self.assertNotEqual(self.submit(data).status_code, 200, key)

    def test_oversize_is_rejected_before_json_parsing(self):
        with patch(
            "processing.views.json.loads", side_effect=AssertionError("parsed oversized body")
        ):
            response = self.client.post(
                URL,
                b"x" * 16385,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fleet",
                HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
            )
        self.assertEqual(response.status_code, 400)

    def test_json_integer_decoding_limit_returns_invalid_request(self):
        data = self.data | {"host": {}, "container": None, "runtime": None}
        raw = json.dumps(data, separators=(",", ":")).replace(
            '"sequence":1', '"sequence":' + "9" * 5000
        )
        # Keep the raw numeric token below the transport cap; never serialize a giant int.
        body = raw.encode().ljust(5413, b" ")
        self.assertEqual(len(body), 5413)
        with patch("processing.views.worker_pool_telemetry.receive") as receive:
            response = self.client.post(
                URL,
                body,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fleet",
                HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        receive.assert_not_called()

    def test_duplicates_and_replays_cannot_refresh_or_regress(self):
        from processing.models import WorkerPoolTelemetry

        self.assertEqual(self.submit().status_code, 200)
        old = WorkerPoolTelemetry.objects.get().received_at
        self.assertEqual(self.submit().json(), {"accepted": True, "duplicate": True})
        self.assertEqual(WorkerPoolTelemetry.objects.get().received_at, old)
        data = copy.deepcopy(self.data)
        data["sequence"] = 2
        data["sampled_at"] = timezone.now().isoformat()
        data["runtime"]["aggregates"]["selfie_query"]["callback_delivered"] = [
            1,
            1,
            [0, 1, 1, 1, 1, 1, 1, 1],
        ]
        self.assertEqual(self.submit(data).status_code, 400)
        data = copy.deepcopy(self.data)
        data["sequence"] = 2
        data["collector_started_at"] = self.now.isoformat()
        self.assertEqual(self.submit(data).status_code, 400)
        data["collector_epoch"] = str(uuid4())
        data["sampled_at"] = timezone.now().isoformat()
        self.assertEqual(self.submit(data).status_code, 200)
        self.assertNotEqual(self.submit().status_code, 200)

    def test_runtime_failure_keeps_host_and_previous_counter_fence(self):
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 2
        self.data["sampled_at"] = timezone.now().isoformat()
        runtime = self.data["runtime"]
        self.data["runtime"] = None
        self.assertEqual(self.submit().status_code, 200)
        samples = self.samples()
        self.assertEqual(samples["worker_runtime_scrape_available"].value, 0)
        self.assertEqual(samples["worker_host_observation_fresh"].value, 1)
        self.data["sequence"] = 3
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["runtime"] = runtime
        runtime["aggregates"]["selfie_query"]["callback_delivered"][0] = 1
        self.assertEqual(self.submit().status_code, 400)

    def test_staged_host_before_registration_never_creates_member(self):
        WorkerPoolMember.objects.all().delete()
        self.pool.staged_build = "b" * 40
        self.pool.observed_members[0]["worker_build"] = "b" * 40
        self.pool.save()
        self.data["worker_build"] = "b" * 40
        self.data["runtime"] = None
        self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(WorkerPoolMember.objects.count(), 0)
        self.assertNotIn("worker_coordinator_ready", self.samples())

    def test_stale_capacity_is_unknown_and_removed_instances_disappear(self):
        self.assertEqual(self.submit().status_code, 200)
        self.pool.observation_completed_at = self.now - timedelta(seconds=91)
        self.pool.save()
        self.assertNotEqual(self.submit().status_code, 200)
        self.assertNotIn("worker_host_memory_available_bytes", self.samples())
        self.pool.observation_completed_at = timezone.now()
        self.pool.observed_members = []
        self.pool.save()
        self.assertFalse(any("instance_id" in sample.labels for sample in self.all_samples()))

    def test_removed_instance_stays_absent_when_complete_cloud_observation_expires(self):
        self.assertEqual(self.submit().status_code, 200)
        lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="group",
            sequence=2,
            started_at=self.now,
            completed_at=self.now,
            target_size=1,
            members=[{"instance_id": "node-2", "status": "RUNNING_ACTUAL", "worker_build": BUILD}],
            complete=True,
        )
        for now in (self.now, self.now + timedelta(seconds=91)):
            with patch("django.utils.timezone.now", return_value=now):
                samples = self.all_samples()
            node_samples = [sample for sample in samples if "instance_id" in sample.labels]
            self.assertTrue(node_samples)
            self.assertTrue(
                all(sample.labels["instance_id"] == "node-2" for sample in node_samples)
            )

    def test_idle_empty_membership_stays_empty_when_cloud_observation_expires(self):
        self.assertEqual(self.submit().status_code, 200)
        lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="group",
            sequence=2,
            started_at=self.now,
            completed_at=self.now,
            target_size=0,
            members=[],
            complete=True,
        )
        for now in (self.now, self.now + timedelta(seconds=91)):
            with patch("django.utils.timezone.now", return_value=now):
                self.assertFalse(
                    any("instance_id" in sample.labels for sample in self.all_samples())
                )

    def test_reboot_and_process_reset_have_explicit_boundaries_and_reject_old_sources(self):
        self.assertEqual(self.submit().status_code, 200)
        old = copy.deepcopy(self.data)
        self.identity = lifecycle.MemberIdentity("selfie", "node-1", uuid4(), BUILD)
        registered = lifecycle.register(self.identity)
        self.data["boot_id"] = str(self.identity.boot_id)
        self.data["collector_epoch"] = str(uuid4())
        self.data["collector_started_at"] = timezone.now().isoformat()
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["runtime"]["registration_generation"] = registered["registration_generation"]
        self.data["runtime"]["sampled_at"] = self.data["sampled_at"]
        self.data["runtime"]["aggregates"] = {
            "selfie_query": {"callback_delivered": [0, 0, [0] * 8]}
        }
        self.assertEqual(self.submit().status_code, 200)
        samples = self.samples()
        self.assertEqual(samples["worker_runtime_executions_total"].value, 0)
        self.assertAlmostEqual(
            samples["worker_runtime_reset_timestamp_seconds"].value,
            timezone.datetime.fromisoformat(self.data["sampled_at"]).timestamp(),
        )
        old["sampled_at"] = timezone.now().isoformat()
        old["sequence"] = 100
        self.assertNotEqual(self.submit(old).status_code, 200)

    def test_zone_cannot_amplify_labels_on_same_source(self):
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 2
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["zone_id"] = "ru-central1-b"
        self.assertEqual(self.submit().status_code, 400)

    def test_container_collection_failure_is_unknown_while_host_stays_fresh(self):
        self.data["container"] = None
        self.data["runtime"] = None
        self.assertEqual(self.submit().status_code, 200)
        samples = self.samples()
        self.assertEqual(samples["worker_container_observation_available"].value, 0)
        self.assertNotIn("worker_container_present", samples)
        self.assertEqual(samples["worker_host_observation_fresh"].value, 1)

    def test_diagnostic_rejection_does_not_break_real_lease_or_terminal_submission(self):
        from processing.models import PhotoProcessingState, ProcessingAttempt, ProcessingJob
        from processing.services.enrollment import request_capture_metadata
        from processing.services.jobs import claim_job, fail_attempt, heartbeat_attempt
        from processing.services.worker_pool_metrics import observe_metrics

        event = Event.objects.create(
            name="Telemetry isolation",
            slug="telemetry-isolation",
            start_date=date.today(),
            end_date=date.today(),
            timezone_name="Europe/Moscow",
        )
        photo = Photo.objects.create(
            id="telemetry-isolation",
            event=event,
            src="",
            original_key="originals/" + "a" * 32,
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            uploaded_by=get_user_model().objects.create_user(username="telemetry-test"),
            original_filename="photo.jpg",
        )
        request_capture_metadata(photo)
        claimed = claim_job(
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
            worker_build=BUILD,
            lease_seconds=120,
        )
        assert isinstance(claimed, ClaimedJob)
        before = {
            "attempts": list(ProcessingAttempt.objects.values()),
            "jobs": list(ProcessingJob.objects.values()),
            "states": list(PhotoProcessingState.objects.values()),
        }
        demand_before = [
            m
            for m in observe_metrics("ru-central1-a")["metrics"]
            if m["name"] in {"worker_pool_workload", "worker_pool_active_leases"}
        ]
        invalid = copy.deepcopy(self.data)
        invalid["runtime"]["registration_generation"] = str(uuid4())
        self.assertEqual(self.submit(invalid).status_code, 503)
        self.assertEqual(
            before,
            {
                "attempts": list(ProcessingAttempt.objects.values()),
                "jobs": list(ProcessingJob.objects.values()),
                "states": list(PhotoProcessingState.objects.values()),
            },
        )
        self.assertEqual(
            demand_before,
            [
                m
                for m in observe_metrics("ru-central1-a")["metrics"]
                if m["name"] in {"worker_pool_workload", "worker_pool_active_leases"}
            ],
        )
        self.assertIsNotNone(heartbeat_attempt(claimed.attempt.id, lease_seconds=120))
        completed = fail_attempt(claimed.attempt.id, error_code="decode_failed", retryable=False)
        self.assertEqual(completed.attempt.status, "failed")
        self.assertEqual(ProcessingJob.objects.get(pk=claimed.job.id).status, "failed")

    def test_missing_container_cannot_erase_restart_counter_fence(self):
        self.assertEqual(self.submit().status_code, 200)
        original = copy.deepcopy(self.data["container"])
        self.data["sequence"] = 2
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["container"] = {"present": False, "running": False, "events_available": False}
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 3
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["container"] = original
        original["restart_count"] = 0
        self.assertEqual(self.submit().status_code, 400)

    def test_container_recreation_accepts_reset_without_container_id_metric_label(self):
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 2
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["container"]["container_id"] = "d" * 64
        self.data["container"]["restart_count"] = 0
        self.assertEqual(self.submit().status_code, 200)
        samples = self.samples()
        self.assertEqual(samples["worker_container_restart_count"].value, 0)
        self.assertAlmostEqual(
            samples["worker_container_reset_timestamp_seconds"].value,
            timezone.datetime.fromisoformat(self.data["sampled_at"]).timestamp(),
        )

    def test_bulk_maximum_all_kind_outcome_snapshot_is_accepted_and_exported(self):
        self.pool.name = "bulk"
        self.pool.save()
        self.data["pool"] = "bulk"
        self.data["runtime"]["aggregates"] = {
            kind: {
                outcome: [2**53, 9007199254740991.0, [2**53] * 8]
                for outcome in (
                    "callback_delivered",
                    "execution_failed",
                    "transport_failed",
                    "lease_lost",
                )
            }
            for kind in (
                "capture_metadata",
                "generate_preview",
                "generate_watermarked_preview",
                "face_embedding",
                "bib_recognition",
            )
        }
        self.assertLess(len(json.dumps(self.data, separators=(",", ":")).encode()), 16384)
        self.assertEqual(self.submit().status_code, 200)
        response = self.client.get("/worker-diagnostics/metrics/")
        counters = [
            sample
            for family in text_string_to_metric_families(response.content.decode())
            for sample in family.samples
            if sample.name == "worker_runtime_executions_total"
        ]
        self.assertEqual(len(counters), 20)
        self.assertTrue(
            all(sample.value == 2**53 and sample.labels["pool"] == "bulk" for sample in counters)
        )

    def test_delayed_sequence_with_later_clock_cannot_replace_latest_snapshot(self):
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 2
        self.data["sampled_at"] = timezone.now().isoformat()
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 1
        self.data["sampled_at"] = timezone.now().isoformat()
        self.assertEqual(self.submit().status_code, 400)

    def test_unavailable_restart_value_cannot_erase_same_container_counter_fence(self):
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 2
        self.data["sampled_at"] = timezone.now().isoformat()
        del self.data["container"]["restart_count"]
        self.assertEqual(self.submit().status_code, 200)
        self.data["sequence"] = 3
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["container"]["restart_count"] = 0
        self.assertEqual(self.submit().status_code, 400)

    def test_alternate_boot_uuid_representation_cannot_forge_counter_reset(self):
        self.assertEqual(self.submit().status_code, 200)
        self.data["boot_id"] = self.identity.boot_id.hex
        self.data["collector_epoch"] = str(uuid4())
        self.data["collector_started_at"] = timezone.now().isoformat()
        self.data["sampled_at"] = timezone.now().isoformat()
        self.data["runtime"]["aggregates"] = {
            "selfie_query": {"callback_delivered": [0, 0, [0] * 8]}
        }
        self.assertEqual(self.submit().status_code, 400)

    def test_source_expiry_omits_resources_even_when_cloud_and_receipt_are_current(self):
        from processing.models import WorkerPoolTelemetry

        self.assertEqual(self.submit().status_code, 200)
        later = self.now + timedelta(seconds=91)
        self.pool.observation_completed_at = later
        self.pool.save()
        WorkerPoolTelemetry.objects.update(received_at=later)
        with patch("django.utils.timezone.now", return_value=later):
            samples = self.samples()
        self.assertEqual(samples["worker_cloud_observation_fresh"].value, 1)
        self.assertEqual(samples["worker_host_observation_fresh"].value, 0)
        self.assertEqual(samples["worker_runtime_observation_fresh"].value, 0)
        self.assertNotIn("worker_host_memory_available_bytes", samples)
        self.assertNotIn("worker_runtime_executions_total", samples)
