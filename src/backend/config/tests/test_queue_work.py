from datetime import timedelta
from io import StringIO
from time import monotonic_ns
from unittest.mock import patch

import psycopg
from commerce.delivery import (
    claim_due_email_deliveries,
    resend_order_access,
    send_claimed_email_delivery,
)
from commerce.email_sender import EmailSendOutcome
from commerce.models import PaymentAttempt
from commerce.payments import reconcile_payment_attempt
from commerce.tests import test_checkout, test_delivery
from commerce.worker import claim_due_payment_reconciliations
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from feature_flags.states import FEATURE_FLAG_OFF
from feature_flags.testing import override_feature_flags
from ingestion.services.imports import claim_import_work, create_import, record_import_failure
from ingestion.tests import test_import_services
from picflow.archive_jobs import process_next_archive, prune_archive_jobs
from picflow.models import PhotoArchiveJob
from picflow.tests import test_archive_jobs
from processing.contracts import ClaimedJob
from processing.models import ProcessingJob, QueueWorkCounter
from processing.services.enrollment import capture_metadata_configuration, request_processor
from processing.services.jobs import claim_job, complete_attempt, fail_attempt
from processing.tests import test_jobs as photo_fixtures
from prometheus_client import CollectorRegistry
from selfie_search.images import PreparedSelfie
from selfie_search.services.face_search_queue import claim, recover_processing, run_once
from selfie_search.services.jobs import ClaimedSearchJob, claim_search_job, complete_search_attempt
from selfie_search.services.submission import submit_selfie_search
from selfie_search.tests import test_gallery_queue, test_submission

from config.queue_telemetry import QueueTelemetry
from config.queue_work_metrics import QueueWorkCounters
from config.tests.test_queue_telemetry import samples


def work(queue: str, processor: str = "") -> tuple[int, int, int, int]:
    return (
        QueueWorkCounter.objects.filter(queue=queue, processor=processor)
        .values_list("waiting", "processing", "retry_wait", "paused")
        .first()
    ) or (0, 0, 0, 0)


class PhotoQueueWorkTests(TestCase):
    setUp = photo_fixtures.ProcessingJobServiceTests.setUp
    private_photo = photo_fixtures.ProcessingJobServiceTests.private_photo

    def test_new_processor_is_visible_through_claim_future_retry_and_completion(self):
        processor = "a_new_photo_processor_with_a_long_name"
        photo = self.private_photo("all-queues")
        with self.captureOnCommitCallbacks(execute=True):
            request_processor(
                photo,
                contract_version=1,
                processor_type=processor,
                processor_version=1,
                configuration=capture_metadata_configuration("Europe/Moscow"),
            )
            request_processor(
                photo,
                contract_version=1,
                processor_type=processor,
                processor_version=1,
                configuration=capture_metadata_configuration("Europe/Moscow"),
            )
        assert work("photo") == work("photo", processor) == (1, 0, 0, 0)
        now = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            first_claim = claim_job(
                contract_version=1,
                processor_type=processor,
                processor_version=1,
                now=now,
            )
        assert isinstance(first_claim, ClaimedJob)
        assert work("photo") == work("photo", processor) == (0, 1, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            fail_attempt(
                first_claim.attempt.id,
                error_code="network_interruption",
                retryable=True,
                now=now + timedelta(seconds=1),
                jitter=lambda _start, _end: 0,
            )
        first_claim.job.refresh_from_db()
        assert first_claim.job.available_at > now
        assert work("photo") == work("photo", processor) == (0, 0, 1, 0)

        with self.captureOnCommitCallbacks(execute=True):
            retry = claim_job(
                contract_version=1,
                processor_type=processor,
                processor_version=1,
                now=first_claim.job.available_at,
            )
        assert isinstance(retry, ClaimedJob)
        with self.captureOnCommitCallbacks(execute=True):
            completed_at = first_claim.job.available_at + timedelta(seconds=2)
            complete_attempt(retry.attempt.id, result={}, now=completed_at)
            duplicate = complete_attempt(retry.attempt.id, result={}, now=completed_at)
        assert duplicate.idempotent
        assert work("photo") == work("photo", processor) == (0, 0, 0, 0)


class SearchQueueWorkTests(TestCase):
    setUp = test_gallery_queue.GalleryQueueTests.setUp
    make_event = test_gallery_queue.GalleryQueueTests.make_event
    make_eligible_embedding = test_gallery_queue.GalleryQueueTests.make_eligible_embedding
    create_search = test_gallery_queue.GalleryQueueTests.create_search

    def test_matching_queue_recovers_only_the_source_claimed_by_the_worker(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.create_search()
        assert work("gallery-search") == (1, 0, 0, 0)
        assert work("selfie_face_search") == (0, 0, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            claimed = claim()
        assert claimed is not None
        assert work("gallery-search") == (0, 1, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            assert recover_processing() == 1
        assert work("gallery-search") == (1, 0, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            assert run_once()
        assert work("gallery-search") == (0, 0, 0, 0)

    def test_recognition_completion_moves_work_to_selfie_matching(self):
        with self.captureOnCommitCallbacks(execute=True):
            submit_selfie_search(
                event=self.event,
                selfie=PreparedSelfie(
                    content=b"prepared",
                    content_type="image/jpeg",
                    source_size=8,
                    source_format="jpeg",
                ),
                storage=test_submission.RecordingStorage(),
                user=self.user,
            )
        assert work("selfie") == (1, 0, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            claimed = claim_search_job(
                contract_version=1, processor_type="selfie_query", processor_version=2
            )
        assert isinstance(claimed, ClaimedSearchJob)
        assert work("selfie") == (0, 1, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            complete_search_attempt(
                claimed.attempt.id,
                result={"model": "adaface-ir18-webface4m", "embedding": [1.0] + [0.0] * 511},
            )
        assert work("selfie") == (0, 0, 0, 0)
        assert work("selfie_face_search") == (1, 0, 0, 0)
        with self.captureOnCommitCallbacks(execute=True):
            assert run_once()
        assert work("selfie_face_search") == (0, 0, 0, 0)


class ImportQueueWorkTests(TestCase):
    setUp = test_import_services.ImportServiceTests.setUp

    def test_gate_off_preserves_waiting_batch_and_exposes_pause_and_failure(self):
        from feature_flags.registry import YANDEX_DISK_IMPORT

        with override_feature_flags(self.flags), self.captureOnCommitCallbacks(execute=True):
            create_import(
                actor=self.owner,
                event=self.event,
                folder=self.folder,
                submitted_source_key="source",
                submission_key="submission",
            )
        with override_feature_flags({YANDEX_DISK_IMPORT: FEATURE_FLAG_OFF}):
            assert work("import") == (1, 0, 0, 0)
            with self.captureOnCommitCallbacks(execute=True):
                assert claim_import_work().kind == "empty"
            assert work("import") == (0, 0, 0, 1)
        with override_feature_flags(self.flags):
            with self.captureOnCommitCallbacks(execute=True):
                claimed = claim_import_work()
            assert claimed.attempt_id is not None
            assert work("import") == (0, 1, 0, 0)
            with (
                patch("config.queue_work.notify_queue_result") as notify,
                self.captureOnCommitCallbacks(execute=True),
            ):
                record_import_failure(
                    attempt_id=claimed.attempt_id,
                    operation="manifest",
                    code="provider_error",
                    retryable=False,
                )
        notify.assert_called_once_with("disk_import", "failed", None, count=1)
        assert work("import") == (0, 0, 0, 0)


class ArchiveQueueWorkTests(TransactionTestCase):
    _job = test_archive_jobs.ArchiveJobTests._job

    def test_archive_counts_follow_deduplicated_enqueue_and_active_prune(self):
        job = self._job()
        assert self._job() == job
        with transaction.atomic():
            self._job(scope="rolled-back")
            transaction.set_rollback(True)
        assert work("photo_archive") == (1, 0, 0, 0)
        PhotoArchiveJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(days=2)
        )
        prune_archive_jobs()
        assert work("photo_archive") == (0, 0, 0, 0)
        assert not PhotoArchiveJob.objects.filter(pk=job.pk).exists()

    def test_archive_claim_is_visible_during_build_and_removed_on_finish(self):
        self._job()

        def build_and_upload(_job):
            assert work("photo_archive") == (0, 1, 0, 0)
            return monotonic_ns()

        with patch("picflow.archive_jobs._build_and_upload", side_effect=build_and_upload):
            assert process_next_archive()
        assert work("photo_archive") == (0, 0, 0, 0)


class EmailQueueWorkTests(TransactionTestCase):
    setUp = test_delivery.EmailDeliveryServiceTests.setUp
    signing_secret = test_delivery.EmailDeliveryServiceTests.signing_secret
    support_contact = test_delivery.EmailDeliveryServiceTests.support_contact

    def test_resend_remains_visible_while_only_a_future_retry_exists(self):
        delivery = resend_order_access(order_id=self.order.pk, now=self.now)
        assert work("commerce_email") == (1, 0, 0, 0)
        [claimed] = claim_due_email_deliveries(now=self.now, limit=1)
        assert work("commerce_email") == (0, 1, 0, 0)
        send_claimed_email_delivery(
            claim=claimed,
            email_sender=test_delivery._RecordingSender((EmailSendOutcome.RETRYABLE_FAILURE,)),
            order_access_url_for_grant=test_delivery._AccessUrlBuilder(),
            order_access_signing_secret=self.signing_secret,
            timeout_seconds=5,
            support_contact=self.support_contact,
            now=self.now,
        )
        assert claim_due_email_deliveries(now=self.now, limit=1) == ()
        assert work("commerce_email") == (0, 0, 1, 0)
        assert work("commerce_payment") == (0, 0, 0, 0)
        delivery.refresh_from_db()
        later = delivery.next_attempt_at
        [retry] = claim_due_email_deliveries(now=later, limit=1)
        with patch("config.queue_work.notify_queue_result") as publish:
            for _ in range(2):
                send_claimed_email_delivery(
                    claim=retry,
                    email_sender=test_delivery._RecordingSender((EmailSendOutcome.SUCCEEDED,)),
                    order_access_url_for_grant=test_delivery._AccessUrlBuilder(),
                    order_access_signing_secret=self.signing_secret,
                    timeout_seconds=5,
                    support_contact=self.support_contact,
                    now=later + timedelta(seconds=3),
                )
        publish.assert_called_once_with(
            "commerce_email",
            "success",
            {"wait": 0, "retry": 60_000, "prepare": 3000, "send": 0},
            count=1,
        )


class PaymentQueueWorkTests(TransactionTestCase):
    setUp = test_checkout.CheckoutServiceTests.setUp
    cart_token = test_checkout.CheckoutServiceTests.cart_token
    gateway = test_checkout.CheckoutServiceTests.gateway
    purchasable = test_checkout.CheckoutServiceTests.purchasable
    checkout = test_checkout.CheckoutServiceTests.checkout

    def test_pending_payment_claim_recovery_and_expiry_are_counted(self):
        gateway = self.gateway()
        with self.purchasable(self.first_photo):
            result = self.checkout(gateway=gateway)
        assert work("commerce_payment") == (1, 0, 0, 0)
        attempt = result.payment_attempt
        now = attempt.expires_at
        assert now is not None
        [claimed] = claim_due_payment_reconciliations(
            now=now, limit=1, adapter_key=attempt.adapter_key
        )
        assert work("commerce_payment") == (0, 1, 0, 0)
        now += timedelta(minutes=6)
        [recovered] = claim_due_payment_reconciliations(
            now=now, limit=1, adapter_key=attempt.adapter_key
        )
        assert recovered.lease_id != claimed.lease_id
        assert work("commerce_payment") == (0, 1, 0, 0)
        with patch("config.queue_work.notify_queue_result") as publish:
            reconcile_payment_attempt(
                attempt_id=attempt.pk,
                gateway=gateway,
                now=now,
                expected_reconciliation_lease_id=recovered.lease_id,
            )
        [call] = publish.call_args_list
        assert call.args[:2] == ("commerce_payment", "success")
        segments = call.args[2]
        assert segments["wait"] > 0 and segments["interval"] >= 360_000
        attempt.refresh_from_db()
        assert attempt.status == PaymentAttempt.Status.EXPIRED
        assert work("commerce_payment") == (0, 0, 0, 0)


class QueueWorkObservationTests(TransactionTestCase):
    setUp = photo_fixtures.ProcessingJobServiceTests.setUp
    private_photo = photo_fixtures.ProcessingJobServiceTests.private_photo
    processor = "capture_metadata"

    def enqueue(self):
        photo = self.private_photo("queue-observation")
        state = request_processor(
            photo,
            contract_version=1,
            processor_type=self.processor,
            processor_version=1,
            configuration=capture_metadata_configuration("Europe/Moscow"),
        )
        assert state.current_job is not None
        return state.current_job

    def claim(self, now):
        claim = claim_job(
            contract_version=1,
            processor_type=self.processor,
            processor_version=1,
            now=now,
        )
        assert isinstance(claim, ClaimedJob)
        return claim

    def scrape(self, telemetry=None):
        registry = CollectorRegistry()
        registry.register(QueueWorkCounters())
        if telemetry is not None:
            registry.register(telemetry)
        return samples(registry)

    def test_first_claim_to_terminal_includes_retry_despite_real_counter_lock_timeout(self):
        job = self.enqueue()
        first = self.claim(job.created_at + timedelta(seconds=2))
        fail_attempt(
            first.attempt.id,
            error_code="network_interruption",
            retryable=True,
            now=job.created_at + timedelta(seconds=3),
            jitter=lambda _start, _end: 0,
        )
        job.refresh_from_db()
        last = self.claim(job.available_at)
        completed_at = job.created_at + timedelta(seconds=40)
        connection_args = connection.get_connection_params()
        with (
            psycopg.connect(**connection_args, autocommit=True) as listener,
            psycopg.connect(**connection_args) as locker,
        ):
            listener.execute("LISTEN queue_telemetry")
            locker.execute(
                "SELECT 1 FROM processing_queueworkcounter "
                "WHERE queue = 'photo' AND processor = '' FOR UPDATE"
            )

            def complete():
                return complete_attempt(
                    last.attempt.id,
                    result={},
                    now=completed_at,
                    download_duration_ms=1000,
                    compute_duration_ms=2000,
                    total_duration_ms=4000,
                )

            completed = complete()
            assert completed.attempt.accepted
            duplicate = complete()
            assert duplicate.idempotent
            notifications = list(listener.notifies(timeout=0.2))

        assert work("photo", self.processor) == (0, 1, 0, 0)
        job.refresh_from_db()
        assert job.status == ProcessingJob.Status.SUCCEEDED
        telemetry = QueueTelemetry()
        for notification in notifications:
            telemetry.record(notification.payload)
        registry = CollectorRegistry()
        registry.register(telemetry)
        observed = {
            (name, dict(labels).get("outcome") or dict(labels).get("segment")): value
            for (name, labels), value in samples(registry).items()
            if dict(labels).get("queue") == "photo_capture_metadata"
        }
        assert observed["findme_queue_results_total", "success"] == 1
        assert observed["findme_queue_results_total", "failed"] == 0
        assert observed["findme_queue_duration_observations_total", None] == 1
        duration = "findme_queue_segment_duration_seconds_total"
        assert observed[duration, "wait"] == 2
        assert observed[duration, "download"] == 1
        assert observed[duration, "compute"] == 2
        assert observed[duration, "retry"] > 0
        assert sum(value for (name, _), value in observed.items() if name == duration) == 40

    def test_scrape_only_reads_counters_and_survives_a_reader_restart(self):
        job = self.enqueue()
        self.claim(job.created_at + timedelta(seconds=2))
        uninitialized = self.scrape()
        assert (
            uninitialized["findme_queue_work_available", (("queue", "photo_capture_metadata"),)]
            == 0
        )
        assert len(uninitialized) == 26

        call_command("rebuild_queue_work_counters", stdout=StringIO())
        # A lost addition for a different processor must leave durable signed counts
        # and must not hide the known live work of this processor in the overview.
        QueueWorkCounter.objects.create(queue="photo", processor="missed", processing=-1)
        QueueWorkCounter.objects.filter(queue="photo", processor="").update(processing=0)
        selects = []

        def observe_reads(execute, sql, params, many, context):
            if sql.lstrip().startswith("SELECT"):
                selects.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(observe_reads):
            first = self.scrape()
            second = self.scrape()
        assert {
            key: value for key, value in first.items() if "observed_timestamp" not in key[0]
        } == {key: value for key, value in second.items() if "observed_timestamp" not in key[0]}
        assert len(selects) == 2
        assert all('FROM "processing_queueworkcounter"' in sql for sql in selects)
        assert connection.connection is None
        assert first["findme_queue_work_available", (("queue", "photo_capture_metadata"),)] == 1
        states = {
            (dict(labels)["queue"], dict(labels)["state"]): value
            for (name, labels), value in first.items()
            if name == "findme_queue_work_count"
        }
        assert states["photo_capture_metadata", "processing"] == 1
        assert states["photo_face_embedding", "processing"] == 0
        assert all(queue != "photo_missed" for queue, state in states)
        assert len(states) == 13 * 4
        assert work("photo", "missed") == (0, -1, 0, 0)

    def test_unavailable_counter_table_does_not_hide_the_completion_listener(self):
        telemetry = QueueTelemetry()
        telemetry.set_listening(True)

        def fail_read(execute, sql, params, many, context):
            if 'FROM "processing_queueworkcounter"' in sql:
                return execute("SELECT 1 / 0", None, False, context)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_read):
            exported = self.scrape(telemetry)
        assert exported["findme_queue_results_listener_connected", ()] == 1
        assert (
            exported[
                "findme_queue_results_observed_timestamp_seconds",
                (("queue", "photo_capture_metadata"),),
            ]
            > 0
        )
        exported = {
            key: value for key, value in exported.items() if key[0] == "findme_queue_work_available"
        }
        assert len(exported) == 13
        assert all(
            name == "findme_queue_work_available" and value == 0
            for (name, _labels), value in exported.items()
        )

    def test_manual_rebuild_reads_only_active_indexes_and_retains_snapshot_on_failure(self):
        job = self.enqueue()
        self.claim(job.created_at + timedelta(seconds=2))
        # This job would show a waiting count if the historical range were included.
        stale = request_processor(
            self.private_photo("historic"),
            contract_version=1,
            processor_type="historical_processor",
            processor_version=1,
            configuration=capture_metadata_configuration("Europe/Moscow"),
        )
        ProcessingJob.objects.filter(pk=stale.current_job_id).update(status="cancelled")
        plans = {}

        def explain_reads(execute, sql, params, many, context):
            if sql.startswith("SELECT") and "COUNT(" in sql:
                execute("EXPLAIN (FORMAT JSON) " + sql, params, many, context)
                plan = context["cursor"].fetchone()[0]
                _, table, _ = sql.split(" FROM ", 1)[1].split('"', 2)
                plans[table] = (set(params), plan)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(explain_reads):
            call_command("rebuild_queue_work_counters", stdout=StringIO())
        expected = {
            "processing_processingjob": {"queued", "processing", "retry_wait"},
            "selfie_search_facesearchtask": {"queued", "processing"},
            "selfie_search_selfiesearchjob": {"queued", "processing", "retry_wait"},
            "ingestion_importbatch": {"queued", "enumerating", "transferring", "paused"},
            "commerce_emaildelivery": {"pending", "processing", "retry_wait"},
            "commerce_paymentattempt": {"pending"},
            "picflow_photoarchivejob": {"queued", "running"},
        }
        assert {table: params for table, (params, _) in plans.items()} == expected
        assert all(
            "Seq Scan" not in str(plan) and "Index" in str(plan) for _, plan in plans.values()
        )
        assert work("photo") == work("photo", self.processor) == (0, 1, 0, 0)
        assert work("photo", "historical_processor") == (0, 0, 0, 0)
        original = list(QueueWorkCounter.objects.order_by("pk").values())

        def fail_replace(execute, sql, params, many, context):
            if sql.startswith('INSERT INTO "processing_queueworkcounter"'):
                return execute("SELECT 1 / 0", None, False, context)
            return execute(sql, params, many, context)

        with (
            self.assertRaises(CommandError),
            connection.execute_wrapper(fail_replace),
        ):
            call_command("rebuild_queue_work_counters", stdout=StringIO())
        assert list(QueueWorkCounter.objects.order_by("pk").values()) == original
