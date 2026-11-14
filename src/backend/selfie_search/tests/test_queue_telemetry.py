from datetime import timedelta

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
from selfie_search.models import SelfieSearch, SelfieSearchJob
from selfie_search.services.face_search_queue import run_once
from selfie_search.services.jobs import complete_search_attempt
from selfie_search.tests import test_gallery_queue, test_jobs, test_submission


class SearchCompletionTelemetryTests(TransactionTestCase):
    setUp = test_submission.GalleryPhotoSubmissionTests.setUp
    make_event = test_submission.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = test_submission.GalleryPhotoSubmissionTests.make_eligible_embedding
    create_search = test_gallery_queue.GalleryQueueTests.create_search
    make_search = test_jobs.SearchJobTests.make_search
    add_candidate = test_jobs.SearchJobTests.add_candidate
    claim = test_jobs.SearchJobTests.claim
    result = test_jobs.SearchJobTests.result

    def listen(self):
        listener = psycopg.connect(**connection.get_connection_params(), autocommit=True)
        self.addCleanup(listener.close)
        listener.execute("LISTEN queue_telemetry")
        return listener

    def test_successful_gallery_and_selfie_reach_independent_listener_and_metrics(self):
        listener = self.listen()

        gallery = self.create_search()
        self.assertTrue(run_once())

        selfie = self.make_search()
        origin = timezone.now() - timedelta(seconds=10)
        SelfieSearch.objects.filter(pk=selfie.pk).update(created_at=origin)
        SelfieSearchJob.objects.filter(search=selfie).update(available_at=origin)
        claimed = self.claim(selfie, now=origin + timedelta(milliseconds=1500.6))
        self.assertFalse(claimed.empty)
        complete_search_attempt(
            claimed.attempt.id,
            result=self.result(),
            now=origin + timedelta(milliseconds=3000.4),
        )
        self.assertTrue(run_once())
        self.assertFalse(run_once())
        gallery.refresh_from_db()
        selfie.refresh_from_db()
        self.assertEqual(gallery.status, SelfieSearch.Status.READY)
        self.assertEqual(gallery.results.count(), 1)
        self.assertEqual(selfie.status, SelfieSearch.Status.READY)
        self.assertEqual(selfie.results.count(), 2)
        self.assertIsNone(selfie.cleanup_confirmed_at)

        notifications = list(listener.notifies(timeout=2, stop_after=2))
        self.assertEqual(len(notifications), 2)
        self.assertNotEqual(notifications[0].pid, listener.info.backend_pid)

        from config.queue_telemetry import QueueTelemetry

        telemetry = QueueTelemetry()
        registry = CollectorRegistry()
        registry.register(telemetry)
        for notification in notifications:
            telemetry.record(notification.payload)

        counts = {}
        durations: dict[str, dict[str, float]] = {}
        for family in text_string_to_metric_families(generate_latest(registry).decode()):
            for sample in family.samples:
                if sample.name == "findme_queue_completed_total":
                    counts[sample.labels["queue"]] = sample.value
                if sample.name == "findme_queue_segment_duration_seconds_total":
                    durations.setdefault(sample.labels["queue"], {})[sample.labels["segment"]] = (
                        sample.value
                    )
        self.assertEqual(counts, {"gallery_search": 1, "selfie_search": 1})

        gallery_wait = round(
            (gallery.matching_started_at - gallery.created_at).total_seconds() * 1000
        )
        gallery_total = round((gallery.terminal_at - gallery.created_at).total_seconds() * 1000)
        self.assertEqual(
            durations["gallery_search"],
            {
                "matching_wait": gallery_wait / 1000,
                "matching_elapsed": (gallery_total - gallery_wait) / 1000,
            },
        )
        selfie_match = round((selfie.matching_started_at - origin).total_seconds() * 1000)
        selfie_total = round((selfie.terminal_at - origin).total_seconds() * 1000)
        self.assertEqual(
            durations["selfie_search"],
            {
                "recognition_wait": 1.501,
                "recognition_elapsed": 1.499,
                "matching_wait": (selfie_match - 3000) / 1000,
                "matching_elapsed": (selfie_total - selfie_match) / 1000,
            },
        )

    def test_outer_rollback_discards_notification(self):
        listener = self.listen()
        gallery = self.create_search()
        with transaction.atomic():
            self.assertTrue(run_once())
            self.assertEqual(list(listener.notifies(timeout=0.1)), [])
            transaction.set_rollback(True)
        gallery.refresh_from_db()
        self.assertEqual(gallery.status, SelfieSearch.Status.QUEUED)
        self.assertEqual(list(listener.notifies(timeout=0.1)), [])

    def test_failed_notification_does_not_undo_ready_result(self):
        listener = self.listen()
        gallery = self.create_search()
        publish_attempts = []

        def fail_pg_notify(execute, sql, params, many, context):
            if "pg_notify" in sql:
                publish_attempts.append(True)
                return execute("SELECT 1 / 0", None, False, context)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_pg_notify):
            self.assertTrue(run_once())
        self.assertEqual(publish_attempts, [True])
        gallery.refresh_from_db()
        self.assertEqual(gallery.status, SelfieSearch.Status.READY)
        self.assertEqual(gallery.results.count(), 1)
        self.assertEqual(list(listener.notifies(timeout=0.1)), [])
