from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from threading import Barrier
from threading import Event as ThreadEvent
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo
from selfie_search.models import SelfieSearch, SelfieSearchAttempt, SelfieSearchJob
from selfie_search.services.jobs import fail_search_attempt, heartbeat_search_attempt
from selfie_search.services.submission import _configuration
from selfie_search.storage import StoredTemporarySelfie

from processing.models import WorkerPool, WorkerPoolMember
from processing.services import worker_pool_lifecycle as lifecycle
from processing.services.enrollment import request_capture_metadata
from processing.views import _claim_with_grant

BUILD = "a" * 40
NEXT = "b" * 40


@override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=True)
class LifecycleTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.pool = lifecycle.configure_pool("selfie", group_id="selfie-group", active_build=BUILD)
        self.envelopes = [
            lifecycle.MemberIdentity("selfie", f"instance-{i}", uuid4(), BUILD) for i in range(2)
        ]
        self.observe()
        lifecycle.set_claims_paused("selfie", paused=False)
        self.envelopes = [self.register_session(envelope) for envelope in self.envelopes]
        for envelope in self.envelopes:
            lifecycle.heartbeat(envelope, ready=True, draining=False)
        WorkerPoolMember.objects.update(idle_since=self.now - timedelta(minutes=20))

    def register_session(self, envelope):
        response = lifecycle.register(envelope)
        return replace(
            envelope, registration_generation=UUID(str(response["registration_generation"]))
        )

    @override_settings(PHOTO_PROCESSING_FLEET_TOKEN="fleet")
    def test_actual_worker_main_recovers_transient_ready_heartbeat_on_same_boot(self):
        from photo_worker import __main__ as entrypoint
        from photo_worker.client import ApiError, HttpClient
        from photo_worker.lifecycle import HostIdentity
        from photo_worker.runner import WorkerConfig
        from photo_worker.transport import REMOTE_API_URL

        envelope = self.envelopes[0]
        identity = HostIdentity("selfie", envelope.instance_id, str(envelope.boot_id), BUILD)
        client = HttpClient(REMOTE_API_URL, "fleet", transport="remote")
        ready_calls = []
        served = []

        def post(path, payload):
            response = self.client.post(
                "/internal/photo-processing/v1/" + path,
                payload,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fleet",
                HTTP_X_FINDME_WORKER_TRANSPORT="private-tls",
            )
            self.assertEqual(response.status_code, 200, response.content)
            if path == "members/heartbeat" and payload.get("ready"):
                ready_calls.append(True)
                if len(ready_calls) == 1:
                    # The canonical write committed, but its response was lost.
                    raise ApiError("unavailable", retryable=True)
            return response.json()

        def run(worker):
            self.assertTrue(worker.fleet.can_claim)
            member = WorkerPoolMember.objects.get(instance_id=envelope.instance_id)
            self.assertTrue(member.ready)
            self.assertFalse(member.draining)
            served.append(True)

        with (
            patch.object(client, "post_json", side_effect=post),
            patch.object(
                entrypoint.WorkerConfig,
                "from_env",
                return_value=(
                    WorkerConfig(BUILD, 120, remote_pool="selfie"),
                    client,
                ),
            ),
            patch.object(entrypoint.HostIdentity, "read", return_value=identity),
            patch("photo_worker.lifecycle._warm_models"),
            patch("signal.signal"),
            patch.object(entrypoint.Worker, "run_forever", run),
        ):
            entrypoint.main()
        self.assertEqual(served, [True])
        self.assertEqual(len(ready_calls), 2)
        self.assertFalse(WorkerPoolMember.objects.get(instance_id=envelope.instance_id).draining)

    def observe(self, *, sequence=1, target=1, members=None, started_at=None, complete=True):
        return lifecycle.record_cloud_snapshot(
            "selfie",
            group_id="selfie-group",
            sequence=sequence,
            started_at=started_at or self.now,
            completed_at=timezone.now(),
            target_size=target,
            members=members
            if members is not None
            else [
                {"instance_id": e.instance_id, "status": "RUNNING_ACTUAL", "worker_build": BUILD}
                for e in self.envelopes
            ],
            complete=complete,
        ) and lifecycle.record_queue_observation(
            "selfie", observed_at=timezone.now(), endpoint_available=True
        )

    def test_two_simultaneous_retirees_leave_a_claiming_survivor(self):
        self.assertEqual(connection.vendor, "postgresql")
        barrier = Barrier(2)

        def retire(envelope):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return lifecycle.request_retirement(envelope)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(retire, self.envelopes))
        self.assertEqual(sum(result is not None for result in results), 1)
        survivor = self.envelopes[results.index(None)]
        with lifecycle.claim_admission((1, "selfie_query", 2), survivor) as admission:
            self.assertTrue(admission.allowed)
        self.assertFalse(WorkerPoolMember.objects.get(instance_id=survivor.instance_id).draining)
        identity, path, storage = self.queued_claim("selfie")
        body = {
            "contract_version": identity[0],
            "processor_type": identity[1],
            "processor_version": identity[2],
            "worker_build": BUILD,
            "lease_seconds": 120,
        }
        with patch(path, return_value=storage):
            payload = _claim_with_grant(body, member=survivor)
        self.assertFalse(payload["empty"])
        job = payload["job"]
        assert isinstance(job, dict)
        completion = fail_search_attempt(
            job["attempt_id"],
            error_code="no_face_detected",
            retryable=False,
            storage=storage,
        )
        self.assertEqual(completion.attempt.status, "failed")

    def test_grant_survives_lost_response_target_release_pause_and_stale_observation(self):
        envelope = self.envelopes[0]
        grant = lifecycle.request_retirement(envelope)
        self.assertIsNotNone(grant)
        WorkerPool.objects.filter(pk=self.pool.pk).update(
            active_build=NEXT,
            target_size=2,
            claims_paused=True,
            observation_completed_at=self.now - timedelta(hours=1),
        )
        self.assertEqual(lifecycle.request_retirement(envelope), grant)
        lifecycle.heartbeat(envelope, ready=True, draining=False)
        member = WorkerPoolMember.objects.get(instance_id=envelope.instance_id)
        self.assertFalse(member.ready)
        self.assertTrue(member.draining)
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.register(
                lifecycle.MemberIdentity("selfie", envelope.instance_id, uuid4(), BUILD)
            )

    def test_partial_out_of_order_and_pregrant_snapshots_cannot_reconcile(self):
        grant = lifecycle.request_retirement(self.envelopes[0])
        self.assertIsNotNone(grant)
        self.assertFalse(self.observe(sequence=2, members=[], complete=False))
        self.assertFalse(self.observe(sequence=1, members=[]))
        self.observe(sequence=3, members=[])
        member = WorkerPoolMember.objects.get(instance_id=self.envelopes[0].instance_id)
        self.assertIsNone(member.reconciled_at)
        self.observe(sequence=4, members=[], started_at=timezone.now())
        member.refresh_from_db()
        self.assertIsNotNone(member.reconciled_at)
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.register(self.envelopes[0])

    def test_stale_cloud_queue_paused_and_inflight_deny_new_retirement(self):
        for changed in (
            {"observation_completed_at": self.now - timedelta(hours=1)},
            {"queue_observed_at": self.now - timedelta(hours=1)},
            {"claims_paused": True},
            {
                "observed_members": [
                    {
                        "instance_id": e.instance_id,
                        "status": "STARTING_INSTANCE",
                        "worker_build": BUILD,
                    }
                    for e in self.envelopes
                ]
            },
        ):
            with self.subTest(changed=changed):
                WorkerPool.objects.filter(pk=self.pool.pk).update(**changed)
                self.assertIsNone(lifecycle.request_retirement(self.envelopes[0]))
                self.observe(
                    sequence=WorkerPool.objects.get(pk=self.pool.pk).observation_sequence + 1
                )
                lifecycle.set_claims_paused("selfie", paused=False)

    def test_wrong_identity_build_boot_and_unregistered_member_fail_closed(self):
        for envelope in (
            lifecycle.MemberIdentity("bulk", "instance-0", self.envelopes[0].boot_id, BUILD),
            lifecycle.MemberIdentity("selfie", "unknown", uuid4(), BUILD),
            lifecycle.MemberIdentity("selfie", "instance-0", self.envelopes[0].boot_id, NEXT),
        ):
            with self.subTest(envelope=envelope), self.assertRaises(lifecycle.AdmissionDenied):
                lifecycle.register(envelope)
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.heartbeat(
                lifecycle.MemberIdentity("selfie", "instance-0", uuid4(), BUILD),
                ready=True,
                draining=False,
            )
        with self.assertRaises(lifecycle.AdmissionDenied):
            with lifecycle.claim_admission((1, "capture_metadata", 2), self.envelopes[0]):
                pass

    def test_staging_keeps_old_claims_suppresses_idle_and_serializes_release(self):
        lifecycle.stage_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.assertIsNone(lifecycle.request_retirement(self.envelopes[0]))
        with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[1]) as admission:
            self.assertTrue(admission.allowed)
        grant = lifecycle.reserve_release_retirement(
            self.envelopes[0], active_build=BUILD, staged_build=NEXT
        )
        self.assertIsNotNone(grant)
        self.assertEqual(
            lifecycle.reserve_release_retirement(
                self.envelopes[0], active_build=BUILD, staged_build=NEXT
            ),
            grant,
        )
        self.assertIsNone(
            lifecycle.reserve_release_retirement(
                self.envelopes[1], active_build=BUILD, staged_build=NEXT
            )
        )

    def test_candidate_warms_before_promotion_and_old_claims_are_fenced(self):
        lifecycle.stage_build("selfie", active_build=BUILD, staged_build=NEXT)
        candidate = lifecycle.MemberIdentity("selfie", "instance-1", uuid4(), NEXT)
        self.observe(
            sequence=2,
            target=2,
            members=[
                {"instance_id": "instance-0", "status": "RUNNING_OUTDATED", "worker_build": BUILD},
                {"instance_id": "instance-1", "status": "RUNNING_ACTUAL", "worker_build": NEXT},
            ],
        )
        candidate = self.register_session(candidate)
        lifecycle.heartbeat(candidate, ready=True, draining=False)
        with lifecycle.claim_admission((1, "selfie_query", 2), candidate) as admission:
            self.assertFalse(admission.allowed)
        with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[0]) as admission:
            attempt = self.make_attempt()
            admission.bind(attempt)
        lifecycle.promote_build("selfie", active_build=BUILD, staged_build=NEXT)
        with lifecycle.claim_admission((1, "selfie_query", 2), candidate) as admission:
            self.assertFalse(admission.allowed)
        self.assertIsNone(
            lifecycle.reserve_release_retirement(
                self.envelopes[0], active_build=NEXT, staged_build=None
            )
        )
        SelfieSearchAttempt.objects.filter(pk=attempt.pk).update(
            status="expired", terminal_at=timezone.now()
        )
        with lifecycle.claim_admission((1, "selfie_query", 2), candidate) as admission:
            self.assertTrue(admission.allowed)
        with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[0]) as admission:
            self.assertFalse(admission.allowed)
        grant = lifecycle.reserve_release_retirement(
            self.envelopes[0], active_build=NEXT, staged_build=None
        )
        self.assertIsNotNone(grant)
        # Rollback is another staged transition; the granted old boot cannot be reused.
        lifecycle.stage_build("selfie", active_build=NEXT, staged_build=BUILD)
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.register(self.envelopes[0])
        self.observe(
            sequence=3,
            started_at=timezone.now(),
            members=[
                {"instance_id": "instance-0", "status": "STOPPED", "worker_build": BUILD},
                {"instance_id": "instance-1", "status": "RUNNING_OUTDATED", "worker_build": NEXT},
            ],
        )

        self.observe(
            sequence=4,
            started_at=timezone.now(),
            members=[
                {"instance_id": "instance-0", "status": "RUNNING_ACTUAL", "worker_build": BUILD},
                {"instance_id": "instance-1", "status": "RUNNING_OUTDATED", "worker_build": NEXT},
            ],
        )
        rollback = lifecycle.MemberIdentity("selfie", "instance-0", uuid4(), BUILD)
        rollback = self.register_session(rollback)
        lifecycle.heartbeat(rollback, ready=True, draining=False)
        lifecycle.promote_build("selfie", active_build=NEXT, staged_build=BUILD)
        with lifecycle.claim_admission((1, "selfie_query", 2), rollback) as admission:
            self.assertTrue(admission.allowed)

    def paused_replacement(self):
        lifecycle.set_claims_paused("selfie", paused=True)
        lifecycle.set_claims_paused("selfie", paused=True, local=True)
        lifecycle.stage_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.observe(
            sequence=2,
            target=1,
            members=[
                {"instance_id": "instance-0", "status": "RUNNING_OUTDATED", "worker_build": BUILD},
                {"instance_id": "instance-1", "status": "RUNNING_ACTUAL", "worker_build": NEXT},
            ],
        )
        candidate = self.register_session(
            lifecycle.MemberIdentity("selfie", "instance-1", uuid4(), NEXT)
        )
        lifecycle.heartbeat(candidate, ready=True, draining=False)
        lifecycle.promote_build("selfie", active_build=BUILD, staged_build=NEXT)
        return candidate

    def test_paused_release_retires_old_with_warm_new_member_and_local_claims_paused(self):
        candidate = self.paused_replacement()
        self.assertIsNone(lifecycle.request_retirement(candidate))
        grant = lifecycle.reserve_release_retirement(
            self.envelopes[0], active_build=NEXT, staged_build=None
        )
        self.assertIsNotNone(grant)
        pool = WorkerPool.objects.get(pk=self.pool.pk)
        self.assertTrue(pool.claims_paused)
        self.assertTrue(pool.local_claims_paused)
        self.assertEqual(
            lifecycle.reserve_release_retirement(
                self.envelopes[0], active_build=NEXT, staged_build=None
            ),
            grant,
        )

    def test_paused_release_keeps_remote_live_work_fenced(self):
        candidate = self.paused_replacement()
        attempt = self.make_attempt()
        WorkerPoolMember.objects.filter(instance_id=candidate.instance_id).update(
            active_selfie_attempt=attempt
        )
        self.assertIsNone(
            lifecycle.reserve_release_retirement(
                self.envelopes[0], active_build=NEXT, staged_build=None
            )
        )

    def test_paused_selfie_release_denies_retirement_when_local_pause_is_open(self):
        self.paused_replacement()
        lifecycle.set_claims_paused("selfie", paused=False, local=True)
        self.assertIsNone(
            lifecycle.reserve_release_retirement(
                self.envelopes[0], active_build=NEXT, staged_build=None
            )
        )
        self.assertFalse(WorkerPoolMember.objects.filter(retirement_grant__isnull=False).exists())

    def test_paused_bulk_release_denies_retirement_when_local_pause_is_open(self):
        member = self.bulk_members()[0]
        lifecycle.set_claims_paused("bulk", paused=True)
        lifecycle.set_claims_paused("bulk", paused=False, local=True)
        self.assertIsNone(
            lifecycle.reserve_release_retirement(member, active_build=BUILD, staged_build=None)
        )
        self.assertFalse(WorkerPoolMember.objects.filter(retirement_grant__isnull=False).exists())

    def test_paused_release_denies_retirement_while_unbound_live_work_continues(self):
        self.paused_replacement()
        self.make_attempt()
        self.assertEqual(lifecycle.local_live_leases("selfie"), 1)
        self.assertIsNone(
            lifecycle.reserve_release_retirement(
                self.envelopes[0], active_build=NEXT, staged_build=None
            )
        )

    def test_paused_release_requires_ready_fresh_active_survivor(self):
        candidate = self.paused_replacement()
        for problem in ("unready", "stale", "draining", "wrong-build"):
            with self.subTest(problem=problem):
                WorkerPoolMember.objects.filter(instance_id=candidate.instance_id).update(
                    ready=problem != "unready",
                    draining=problem == "draining",
                    heartbeat_at=timezone.now() - timedelta(minutes=5)
                    if problem == "stale"
                    else timezone.now(),
                    worker_build=BUILD if problem == "wrong-build" else NEXT,
                )
                self.assertIsNone(
                    lifecycle.reserve_release_retirement(
                        self.envelopes[0], active_build=NEXT, staged_build=None
                    )
                )

    def test_local_pause_and_unconfigured_fleet_fail_closed(self):
        lifecycle.set_claims_paused("selfie", paused=True, local=True)
        with lifecycle.claim_admission((1, "selfie_query", 2), None) as admission:
            self.assertFalse(admission.allowed)
        with self.assertRaises(lifecycle.AdmissionDenied):
            with lifecycle.claim_admission((1, "capture_metadata", 2), self.envelopes[0]):
                pass
        with override_settings(DEBUG=True, PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False):
            with lifecycle.claim_admission((1, "unknown", 9), None) as admission:
                self.assertTrue(admission.allowed)
            with self.assertRaises(lifecycle.AdmissionDenied):
                lifecycle.register(self.envelopes[0])

    @override_settings(DEBUG=False)
    def test_production_local_admission_is_denied_even_when_local_pause_is_open(self):
        lifecycle.set_claims_paused("selfie", paused=False, local=True)
        with lifecycle.claim_admission((1, "selfie_query", 2), None) as admission:
            self.assertFalse(admission.allowed)
        with override_settings(PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False):
            with lifecycle.claim_admission((1, "selfie_query", 2), None) as admission:
                self.assertFalse(admission.allowed)

    def test_same_boot_restart_is_cold_until_new_process_warms(self):
        current = self.register_session(self.envelopes[0])
        member = WorkerPoolMember.objects.get(instance_id="instance-0")
        self.assertFalse(member.ready)
        self.assertIsNone(member.heartbeat_at)
        self.assertIsNone(member.idle_since)
        self.assertIsNone(lifecycle.request_retirement(self.envelopes[1]))
        with self.assertRaises(lifecycle.RegistrationChanged):
            lifecycle.heartbeat(self.envelopes[0], ready=True, draining=False)
        with self.assertRaises(lifecycle.RegistrationChanged):
            with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[0]):
                pass
        with lifecycle.claim_admission((1, "selfie_query", 2), current) as admission:
            self.assertFalse(admission.allowed)
        lifecycle.heartbeat(current, ready=True, draining=False)
        with lifecycle.claim_admission((1, "selfie_query", 2), current) as admission:
            self.assertTrue(admission.allowed)

    def test_old_process_heartbeat_waiting_behind_register_cannot_restore_readiness(self):
        entered = ThreadEvent()

        def late_heartbeat():
            close_old_connections()
            try:
                entered.set()
                return lifecycle.heartbeat(self.envelopes[0], ready=True, draining=False)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                self.register_session(self.envelopes[0])
                late = executor.submit(late_heartbeat)
                self.assertTrue(entered.wait(5))
                self.assertFalse(late.done())
            with self.assertRaises(lifecycle.RegistrationChanged):
                late.result(timeout=5)
        self.assertFalse(WorkerPoolMember.objects.get(instance_id="instance-0").ready)

    def make_attempt(self):
        event = Event.objects.create(
            name="test", slug=str(uuid4()), start_date=date.today(), end_date=date.today()
        )
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest=str(uuid4()),
            status="processing",
            temporary_object_key="private",
        )
        job = SelfieSearchJob.objects.create(search=search, status="processing")
        return SelfieSearchAttempt.objects.create(
            job=job, lease_expires_at=self.now + timedelta(minutes=2)
        )

    def test_lost_claim_response_restart_expiry_and_boot_replacement_preserve_ownership(self):
        envelope = self.envelopes[0]
        with lifecycle.claim_admission((1, "selfie_query", 2), envelope) as admission:
            attempt = self.make_attempt()
            admission.bind(attempt)
        envelope = self.register_session(envelope)
        lifecycle.heartbeat(envelope, ready=True, draining=False)
        with lifecycle.claim_admission((1, "selfie_query", 2), envelope) as admission:
            self.assertFalse(admission.allowed)
        self.assertIsNone(lifecycle.request_retirement(envelope))
        SelfieSearchAttempt.objects.filter(pk=attempt.pk).update(
            lease_expires_at=self.now - timedelta(seconds=1)
        )
        # A delayed heartbeat sampled time before the expiry/row-lock boundary. Clock
        # expiry alone must not release the cap, local drain, member pointer or stop fence.
        self.assertIsNone(lifecycle.request_retirement(envelope))
        with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[1]) as admission:
            self.assertFalse(admission.allowed)
        new = lifecycle.MemberIdentity("selfie", envelope.instance_id, uuid4(), BUILD)
        new = self.register_session(new)
        lifecycle.heartbeat(new, ready=True, draining=False)
        self.assertEqual(
            WorkerPoolMember.objects.get(instance_id=envelope.instance_id).active_selfie_attempt_id,
            attempt.pk,
        )
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.request_retirement(envelope)
        SelfieSearchAttempt.objects.filter(pk=attempt.pk).update(
            status="expired", terminal_at=timezone.now()
        )
        with lifecycle.claim_admission((1, "selfie_query", 2), new) as admission:
            self.assertTrue(admission.allowed)

    def test_local_drain_counts_only_unbound_attempts_and_cap_includes_both(self):
        attempt = self.make_attempt()
        self.assertEqual(lifecycle.local_live_leases("selfie"), 1)
        with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[0]) as admission:
            self.assertFalse(admission.allowed)
        WorkerPoolMember.objects.filter(instance_id="instance-0").update(
            active_selfie_attempt=attempt
        )
        self.assertEqual(lifecycle.local_live_leases("selfie"), 0)
        with lifecycle.claim_admission((1, "selfie_query", 2), None) as admission:
            self.assertFalse(admission.allowed)
        with override_settings(PHOTO_WORKER_SELFIE_CLAIM_LIMIT=2):
            with lifecycle.claim_admission((1, "selfie_query", 2), None) as admission:
                self.assertFalse(admission.allowed)

    @override_settings(
        DEBUG=False,
        PHOTO_PROCESSING_ENABLED=True,
        PHOTO_PROCESSING_WORKER_TOKEN="local",
        PHOTO_PROCESSING_FLEET_TOKEN="fleet",
    )
    def test_private_envelope_required_and_lifecycle_endpoints_are_fleet_only(self):
        base = "/internal/photo-processing/v1/"
        body = {
            "contract_version": 1,
            "processor_type": "selfie_query",
            "processor_version": 2,
            "worker_build": BUILD,
            "lease_seconds": 120,
        }
        headers = {
            "HTTP_AUTHORIZATION": "Bearer fleet",
            "HTTP_X_FINDME_WORKER_TRANSPORT": "private-tls",
        }
        for processor, version in (("capture_metadata", 2), ("selfie_query", 2)):
            response = self.client.post(
                base + "claim",
                body | {"processor_type": processor, "processor_version": version},
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer local",
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            base + "claim", body, content_type="application/json", **headers
        )
        self.assertEqual(response.status_code, 400)
        envelope = {
            "pool": "selfie",
            "instance_id": "instance-0",
            "boot_id": str(self.envelopes[0].boot_id),
            "worker_build": BUILD,
        }
        response = self.client.post(
            base + "members/register", envelope, content_type="application/json", **headers
        )
        self.assertEqual(response.status_code, 200)
        stale = envelope | {
            "registration_generation": str(self.envelopes[0].registration_generation)
        }
        response = self.client.post(
            base + "members/heartbeat",
            stale | {"ready": True, "draining": False},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 412)
        response = self.client.post(
            base + "claim", body | stale, content_type="application/json", **headers
        )
        self.assertEqual(response.status_code, 412)
        response = self.client.post(
            base + "members/register",
            envelope,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer local",
        )
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            base + "members/retire",
            envelope | {"reason": "release"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 400)

    def queued_claim(self, pool):
        event = Event.objects.create(
            name=str(uuid4()),
            slug=str(uuid4()),
            timezone_name="Europe/Moscow",
            start_date=date.today(),
            end_date=date.today(),
            face_search_generation=Event.FaceSearchGeneration.SFACE_V3,
        )
        storage = Mock()
        storage.create_download_grant.return_value.url = "https://storage.test/private"
        storage.create_download_grant.return_value.expires_at = timezone.now() + timedelta(
            seconds=30
        )
        if pool == "selfie":
            configuration = _configuration(
                event=event, content_type="image/jpeg", content_size=1024
            )
            search = SelfieSearch.objects.create(
                event=event,
                public_token_digest=str(uuid4()),
                temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
                configuration=configuration,
            )
            SelfieSearchJob.objects.create(search=search, configuration=configuration)
            storage.inspect.return_value = StoredTemporarySelfie(
                key=search.temporary_object_key, size=1024, content_type="image/jpeg"
            )
            return (1, "selfie_query", 2), "processing.views.TemporarySelfieStorage", storage
        photo = Photo.objects.create(
            id=uuid4().hex,
            event=event,
            src="",
            uploaded_by=get_user_model().objects.create_user(username=uuid4().hex),
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_size=1024,
            original_content_type="image/jpeg",
            original_filename="photo.jpg",
            uploaded_at=timezone.now(),
        )
        request_capture_metadata(photo)
        return (1, "capture_metadata", 2), "processing.views._download_storage", storage

    def bulk_members(self):
        lifecycle.configure_pool("bulk", group_id="bulk-group", active_build=BUILD)
        identities = [
            lifecycle.MemberIdentity("bulk", f"bulk-{i}", uuid4(), BUILD) for i in range(2)
        ]
        lifecycle.record_cloud_snapshot(
            "bulk",
            group_id="bulk-group",
            sequence=1,
            started_at=self.now,
            completed_at=timezone.now(),
            target_size=1,
            members=[
                {"instance_id": e.instance_id, "status": "RUNNING_ACTUAL", "worker_build": BUILD}
                for e in identities
            ],
            complete=True,
        )
        lifecycle.record_queue_observation(
            "bulk", observed_at=timezone.now(), endpoint_available=True
        )
        lifecycle.set_claims_paused("bulk", paused=False)
        identities = [self.register_session(envelope) for envelope in identities]
        for envelope in identities:
            lifecycle.heartbeat(envelope, ready=True, draining=False)
        WorkerPoolMember.objects.filter(pool__name="bulk").update(
            idle_since=self.now - timedelta(minutes=20)
        )
        return identities

    def test_claim_and_retire_serialize_with_actual_attempt_binding_in_both_stores(self):
        for pool, member in (("selfie", self.envelopes[0]), ("bulk", self.bulk_members()[0])):
            with self.subTest(pool=pool):
                identity, storage_path, storage = self.queued_claim(pool)
                entered, release, retire_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
                normal_sign = storage.create_download_grant

                def sign(entered=entered, release=release, normal_sign=normal_sign, **kwargs):
                    entered.set()
                    self.assertTrue(release.wait(5))
                    return normal_sign(**kwargs)

                storage.create_download_grant = sign
                body = {
                    "contract_version": identity[0],
                    "processor_type": identity[1],
                    "processor_version": identity[2],
                    "worker_build": BUILD,
                    "lease_seconds": 120,
                }

                def claim(body=body, member=member):
                    close_old_connections()
                    try:
                        return _claim_with_grant(body, member=member)
                    finally:
                        close_old_connections()

                def retire(retire_started=retire_started, member=member):
                    close_old_connections()
                    try:
                        retire_started.set()
                        return lifecycle.request_retirement(member)
                    finally:
                        close_old_connections()

                with (
                    patch(storage_path, return_value=storage),
                    ThreadPoolExecutor(max_workers=2) as executor,
                ):
                    claimed = executor.submit(claim)
                    self.assertTrue(entered.wait(5))
                    retired = executor.submit(retire)
                    self.assertTrue(retire_started.wait(5))
                    self.assertFalse(retired.done())
                    release.set()
                    payload = claimed.result(timeout=5)
                    self.assertIsNone(retired.result(timeout=5))
                    self.assertFalse(payload["empty"])
                    current = WorkerPoolMember.objects.get(instance_id=member.instance_id)
                    bound = current.active_processing_attempt_id or current.active_selfie_attempt_id
                    self.assertEqual(str(bound), payload["job"]["attempt_id"])
                    self.assertTrue(_claim_with_grant(body, member=member)["empty"])

    def test_claim_input_grant_failure_rolls_back_attempt_and_binding(self):
        identity, storage_path, storage = self.queued_claim("selfie")
        storage.create_download_grant.side_effect = RuntimeError("sign failed")
        body = {
            "contract_version": identity[0],
            "processor_type": identity[1],
            "processor_version": identity[2],
            "worker_build": BUILD,
            "lease_seconds": 120,
        }
        with patch(storage_path, return_value=storage), self.assertRaises(RuntimeError):
            _claim_with_grant(body, member=self.envelopes[0])
        self.assertFalse(SelfieSearchAttempt.objects.exists())
        self.assertIsNone(
            WorkerPoolMember.objects.get(instance_id="instance-0").active_selfie_attempt_id
        )

    def test_delayed_heartbeat_after_clock_expiry_keeps_claim_slot_and_retirement_fenced(self):
        identity, storage_path, storage = self.queued_claim("selfie")
        body = {
            "contract_version": identity[0],
            "processor_type": identity[1],
            "processor_version": identity[2],
            "worker_build": BUILD,
            "lease_seconds": 120,
        }
        with patch(storage_path, return_value=storage):
            payload = _claim_with_grant(body, member=self.envelopes[0])
        job = payload["job"]
        assert isinstance(job, dict)
        attempt_id = job["attempt_id"]
        sampled = timezone.now() - timedelta(seconds=10)
        SelfieSearchAttempt.objects.filter(pk=attempt_id).update(
            lease_expires_at=sampled + timedelta(seconds=1)
        )
        started = ThreadEvent()

        def delayed():
            close_old_connections()
            try:
                started.set()
                return heartbeat_search_attempt(attempt_id, now=sampled)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                SelfieSearchAttempt.objects.select_for_update().get(pk=attempt_id)
                heartbeat = executor.submit(delayed)
                self.assertTrue(started.wait(5))
                self.assertIsNone(lifecycle.request_retirement(self.envelopes[0]))
                with lifecycle.claim_admission(identity, self.envelopes[1]) as admission:
                    self.assertFalse(admission.allowed)
            self.assertIsNotNone(heartbeat.result(timeout=5))

    def test_pending_slots_keep_old_claims_fresh_but_cannot_reconcile_disappearance(self):
        lifecycle.stage_build("selfie", active_build=BUILD, staged_build=NEXT)
        grant = lifecycle.reserve_release_retirement(
            self.envelopes[0], active_build=BUILD, staged_build=NEXT
        )
        self.assertIsNotNone(grant)
        self.assertTrue(
            self.observe(
                sequence=2,
                started_at=timezone.now(),
                members=[
                    {
                        "instance_id": "instance-1",
                        "status": "RUNNING_OUTDATED",
                        "worker_build": BUILD,
                    },
                    {"instance_id": "", "status": "CREATING_INSTANCE", "worker_build": ""},
                ],
            )
        )
        self.assertIsNone(WorkerPoolMember.objects.get(instance_id="instance-0").reconciled_at)
        with lifecycle.claim_admission((1, "selfie_query", 2), self.envelopes[1]) as admission:
            self.assertTrue(admission.allowed)

    def test_bounded_local_drain_recovers_expired_attempt_or_aborts_on_live_timeout(self):
        attempt = self.make_attempt()
        lifecycle.set_claims_paused("selfie", paused=True, local=True)
        self.assertFalse(
            lifecycle.wait_for_local_drain("selfie", timeout_seconds=0.01, poll_seconds=0.01)
        )
        SelfieSearchAttempt.objects.filter(pk=attempt.pk).update(
            lease_expires_at=self.now - timedelta(seconds=1)
        )
        self.assertTrue(
            lifecycle.wait_for_local_drain("selfie", timeout_seconds=1, poll_seconds=0.01)
        )
        self.assertEqual(SelfieSearchAttempt.objects.get().status, "expired")

    def test_cancel_staged_build_requires_reconciliation_and_fresh_surviving_active(self):
        lifecycle.stage_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.observe(
            sequence=2,
            members=[
                {"instance_id": "instance-0", "status": "RUNNING_OUTDATED", "worker_build": BUILD},
                {"instance_id": "", "status": "CREATING_INSTANCE", "worker_build": ""},
            ],
        )
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.cancel_staged_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.observe(sequence=3)
        WorkerPool.objects.filter(pk=self.pool.pk).update(
            observation_completed_at=self.now - timedelta(hours=1)
        )
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.cancel_staged_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.observe(sequence=4)
        # No candidate was ever created; a fresh complete snapshot suffices.
        lifecycle.cancel_staged_build("selfie", active_build=BUILD, staged_build=NEXT)
        lifecycle.stage_build("selfie", active_build=BUILD, staged_build=NEXT)
        candidate = lifecycle.MemberIdentity("selfie", "instance-1", uuid4(), NEXT)
        self.observe(
            sequence=5,
            members=[
                {"instance_id": "instance-0", "status": "RUNNING_OUTDATED", "worker_build": BUILD},
                {"instance_id": "instance-1", "status": "RUNNING_ACTUAL", "worker_build": NEXT},
            ],
        )
        candidate = self.register_session(candidate)
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.cancel_staged_build("selfie", active_build=BUILD, staged_build=NEXT)
        grant = lifecycle.reserve_release_retirement(
            candidate, active_build=BUILD, staged_build=NEXT
        )
        self.assertIsNotNone(grant)
        with self.assertRaises(lifecycle.AdmissionDenied):
            lifecycle.cancel_staged_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.observe(
            sequence=6,
            started_at=timezone.now(),
            members=[
                {"instance_id": "instance-0", "status": "RUNNING_OUTDATED", "worker_build": BUILD},
                {"instance_id": "instance-1", "status": "STOPPED", "worker_build": NEXT},
            ],
        )
        lifecycle.cancel_staged_build("selfie", active_build=BUILD, staged_build=NEXT)
        self.pool.refresh_from_db()
        self.assertIsNone(self.pool.staged_build)
        self.assertEqual(lifecycle.request_retirement(candidate), grant)
