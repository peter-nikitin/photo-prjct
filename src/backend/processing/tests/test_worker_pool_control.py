import json
from dataclasses import replace
from datetime import date, timedelta
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

    def test_reactivation_eligibility_and_rebind_clear_stale_readiness(self):
        old = "a" * 40
        new = "b" * 40
        now = timezone.now()
        for name in ("bulk", "selfie"):
            lifecycle.configure_pool(name, group_id=f"{name}-old", active_build=old)
            lifecycle.record_cloud_snapshot(
                name,
                group_id=f"{name}-old",
                sequence=7,
                started_at=now,
                completed_at=now,
                target_size=1,
                complete=True,
                members=[
                    {
                        "instance_id": f"{name}-stale",
                        "status": "RUNNING_ACTUAL",
                        "worker_build": old,
                    }
                ],
            )
            lifecycle.record_queue_observation(name, observed_at=now, endpoint_available=True)
        expected = {
            name: {"group_id": f"{name}-old", "active_build": old} for name in ("bulk", "selfie")
        }
        self.assertEqual(
            self.control("reactivation-eligible", predecessors=expected),
            {"eligible": True},
        )
        for name in ("bulk", "selfie"):
            self.control(
                "rebind",
                pool=name,
                old_group_id=f"{name}-old",
                old_build=old,
                group_id=f"{name}-new",
                active_build=new,
            )
        rows = self.control("status")
        for name in ("bulk", "selfie"):
            row = WorkerPool.objects.get(name=name)
            self.assertEqual((row.group_id, row.active_build), (f"{name}-new", new))
            self.assertEqual(row.observed_members, [])
            self.assertEqual(row.target_size, 0)
            self.assertEqual(row.observation_sequence, 0)
            self.assertIsNone(row.observation_started_at)
            self.assertIsNone(row.observation_completed_at)
            self.assertIsNone(row.queue_observed_at)
            self.assertFalse(row.endpoint_available)
            self.assertTrue(rows[name]["claims_paused"])
            self.assertFalse(rows[name]["local_claims_paused"])
            self.assertFalse(rows[name]["fresh"])
        self.control(
            "rebind",
            pool="bulk",
            old_group_id="bulk-old",
            old_build=old,
            group_id="bulk-new",
            active_build=new,
        )

    def test_reactivation_rejects_changed_identity_members_and_unfinished_attempts(self):
        old = "a" * 40
        lifecycle.configure_pool("bulk", group_id="bulk-old", active_build=old)
        expected = {"bulk": {"group_id": "bulk-old", "active_build": old}}
        with self.assertRaises(CommandError):
            self.control(
                "reactivation-eligible",
                predecessors={"bulk": {"group_id": "wrong", "active_build": old}},
            )
        with self.assertRaises(CommandError):
            self.control(
                "rebind",
                pool="bulk",
                old_group_id="wrong",
                old_build=old,
                group_id="bulk-new",
                active_build="b" * 40,
            )
        from processing.models import WorkerPoolMember

        WorkerPoolMember.objects.create(
            pool=WorkerPool.objects.get(name="bulk"),
            instance_id="live",
            boot_id=uuid4(),
            worker_build=old,
        )
        with self.assertRaises(CommandError):
            self.control("reactivation-eligible", predecessors=expected)
        with self.assertRaises(CommandError):
            self.control(
                "rebind",
                pool="bulk",
                old_group_id="bulk-old",
                old_build=old,
                group_id="bulk-new",
                active_build="b" * 40,
            )
        self.assertEqual(WorkerPool.objects.get(name="bulk").group_id, "bulk-old")

    def test_rebind_preserves_terminal_selfie_attempt_and_result_and_rejects_live_attempt(self):
        from picflow.models import Event, Photo
        from selfie_search.models import (
            SelfieSearch,
            SelfieSearchAttempt,
            SelfieSearchJob,
            SelfieSearchResult,
        )

        old = "a" * 40
        lifecycle.configure_pool("selfie", group_id="selfie-old", active_build=old)
        event = Event.objects.create(
            name="retained", slug="retained", start_date=date.today(), end_date=date.today()
        )
        photo = Photo.objects.create(id="retained-photo", event=event, src="/retained.jpg")
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest="fixture-digest",
            status="ready",
            temporary_object_key="private",
        )
        job = SelfieSearchJob.objects.create(search=search, status="succeeded")
        attempt = SelfieSearchAttempt.objects.create(
            job=job,
            status="succeeded",
            terminal_at=timezone.now(),
            result_hash="d" * 64,
        )
        result = SelfieSearchResult.objects.create(search=search, photo=photo, rank=1)
        self.control(
            "rebind",
            pool="selfie",
            old_group_id="selfie-old",
            old_build=old,
            group_id="selfie-new",
            active_build="b" * 40,
        )
        self.assertEqual(SelfieSearchAttempt.objects.get(pk=attempt.pk).result_hash, "d" * 64)
        self.assertEqual(SelfieSearchResult.objects.get(pk=result.pk).photo_id, photo.pk)
        live_search = SelfieSearch.objects.create(
            event=event,
            public_token_digest="other-digest",
            status="processing",
            temporary_object_key="other-private",
        )
        live_job = SelfieSearchJob.objects.create(search=live_search, status="processing")
        SelfieSearchAttempt.objects.create(job=live_job, lease_expires_at=timezone.now())
        with self.assertRaises(CommandError):
            self.control(
                "rebind",
                pool="selfie",
                old_group_id="selfie-new",
                old_build="b" * 40,
                group_id="selfie-another",
                active_build="c" * 40,
            )
        self.assertEqual(WorkerPool.objects.get(name="selfie").group_id, "selfie-new")
