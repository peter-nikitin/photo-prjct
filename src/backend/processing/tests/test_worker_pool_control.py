import json
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import UUID, uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from processing.models import WorkerPool
from processing.services import worker_pool_lifecycle as lifecycle


@override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
class CanonicalControlTests(TestCase):
    def control(self, operation, **request):
        output = StringIO()
        with patch("sys.stdin", StringIO(json.dumps({"operation": operation, **request}))):
            call_command("control_worker_pools", stdout=output)
        return json.loads(output.getvalue())

    def test_configure_defaults_paused_and_cli_preserves_cas_guards(self):
        self.control("configure", pool="selfie", group_id="selfie-group", active_build="a" * 40)
        self.assertTrue(WorkerPool.objects.get(name="selfie").claims_paused)
        self.control("stage", pool="selfie", active_build="a" * 40, staged_build="b" * 40)
        with self.assertRaises(CommandError):
            self.control("promote", pool="selfie", active_build="a" * 40, staged_build="b" * 40)
        self.assertEqual(WorkerPool.objects.get(name="selfie").active_build, "a" * 40)

    def test_status_readiness_expires_and_same_boot_restart_is_not_warm(self):
        lifecycle.configure_pool("selfie", group_id="selfie-group", active_build="a" * 40)
        now = timezone.now()
        lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="selfie-group",
            sequence=1,
            started_at=now,
            completed_at=now,
            target_size=1,
            complete=True,
            members=[{"instance_id": "node", "status": "RUNNING_ACTUAL", "worker_build": "a" * 40}],
        )
        lifecycle.record_queue_observation("selfie", observed_at=now, endpoint_available=True)
        identity = lifecycle.MemberIdentity("selfie", "node", uuid4(), "a" * 40)
        registered = lifecycle.register(identity)
        identity = replace(
            identity, registration_generation=UUID(registered["registration_generation"])
        )
        lifecycle.heartbeat(identity, ready=True, draining=False)
        self.assertTrue(self.control("status")["selfie"]["members"][0]["warm"])
        lifecycle.register(identity)
        self.assertFalse(self.control("status")["selfie"]["members"][0]["warm"])
        WorkerPool.objects.update(observation_completed_at=now - timedelta(seconds=91))
        self.assertFalse(self.control("status")["selfie"]["fresh"])

    def test_local_drain_requires_pause_and_rejects_unbounded_deadline(self):
        lifecycle.configure_pool("bulk", group_id="bulk-group", active_build="a" * 40)
        with self.assertRaises(CommandError):
            self.control("drain-local", pool="bulk", timeout_seconds=1)
        self.control("pause", pool="bulk", paused=True, local=True)
        self.assertTrue(self.control("drain-local", pool="bulk", timeout_seconds=1)["drained"])
        with self.assertRaises(CommandError):
            self.control("drain-local", pool="bulk", timeout_seconds=901)

    def test_unknown_keys_fail_without_mutation(self):
        with self.assertRaises(CommandError):
            self.control(
                "configure", pool="bulk", group_id="bulk", active_build="a" * 40, token="test-only"
            )
        self.assertFalse(WorkerPool.objects.exists())
