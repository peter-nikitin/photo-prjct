from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from django.test import TestCase, TransactionTestCase
from django.urls import Resolver404, resolve
from django.utils import timezone
from selfie_search.models import SelfieSearch
from selfie_search.tests import test_submission as fixtures


class GalleryQueueTests(TestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding

    def create_search(self):
        from selfie_search.services.submission import submit_gallery_photo_search

        face = self.make_eligible_embedding(
            event=self.event, photo_id=uuid4().hex, vector=[1.0] + [0.0] * 511
        )
        return submit_gallery_photo_search(
            event=self.event,
            photo=face.detection.attempt.photo,
            detection_id=face.detection_id,
            user=self.user,
        ).search

    def test_first_claim_timestamp_survives_live_retry_and_startup_recovery(self):
        from selfie_search.services.gallery_queue import claim, recover_processing, retry

        search = self.create_search()
        self.assertIsNone(search.gallery_started_at)
        first = claim()
        assert first is not None
        started = first.gallery_started_at
        self.assertIsNotNone(started)
        retry(first)
        SelfieSearch.objects.filter(pk=search.pk).update(gallery_available_at=timezone.now())
        second = claim()
        assert second is not None
        self.assertEqual(second.gallery_started_at, started)
        recover_processing()
        third = claim()
        assert third is not None
        self.assertEqual(third.gallery_started_at, started)

    def test_browser_cannot_execute_gallery_search(self):
        with self.assertRaises(Resolver404):
            resolve("/events/gallery/selfie-search/token/process-gallery/")

    def test_worker_finishes_without_browser_and_does_not_claim_selfie(self):
        from selfie_search.services.gallery_queue import run_once

        search = self.create_search()
        selfie = SelfieSearch.objects.create(
            event=self.event, public_token_digest="f" * 64, temporary_object_key="selfie"
        )
        self.assertTrue(run_once())
        search.refresh_from_db()
        selfie.refresh_from_db()
        self.assertEqual(search.status, "ready")
        self.assertEqual(search.gallery_attempt_count, 1)
        self.assertEqual(search.results.count(), 1)
        self.assertEqual(selfie.status, "queued")
        self.assertFalse(run_once())

    def test_startup_reset_changes_only_gallery_processing_and_preserves_ready_snapshot(self):
        from selfie_search.services.gallery_queue import recover_processing, run_once

        ready = self.create_search()
        self.assertTrue(run_once())
        saved_ready = SelfieSearch.objects.filter(pk=ready.pk).values().get()
        saved_rows = list(ready.results.values())
        preserved = []
        for status in ("queued", "failed", "search_unavailable"):
            row = self.create_search()
            SelfieSearch.objects.filter(pk=row.pk).update(status=status, gallery_attempt_count=3)
            preserved.append(SelfieSearch.objects.filter(pk=row.pk).values().get())
        selfie = SelfieSearch.objects.create(
            event=self.event,
            status="processing",
            temporary_object_key="selfie",
            public_token_digest="d" * 64,
        )
        saved_selfie = SelfieSearch.objects.filter(pk=selfie.pk).values().get()
        interrupted = self.create_search()
        SelfieSearch.objects.filter(pk=interrupted.pk).update(
            status="processing",
            gallery_attempt_count=99,
            gallery_available_at=timezone.now() + timedelta(days=1),
        )
        now = timezone.now()
        from unittest.mock import patch

        with patch("selfie_search.services.gallery_queue.timezone.now", return_value=now):
            self.assertEqual(recover_processing(), 1)
        interrupted.refresh_from_db()
        self.assertEqual(interrupted.status, "queued")
        self.assertEqual(interrupted.gallery_attempt_count, 0)
        self.assertEqual(interrupted.gallery_available_at, now)
        self.assertEqual(SelfieSearch.objects.filter(pk=ready.pk).values().get(), saved_ready)
        self.assertEqual(list(ready.results.values()), saved_rows)
        self.assertEqual(SelfieSearch.objects.filter(pk=selfie.pk).values().get(), saved_selfie)
        for snapshot in preserved:
            self.assertEqual(
                SelfieSearch.objects.filter(pk=snapshot["id"]).values().get(), snapshot
            )

    def test_public_submission_is_processed_without_status_request(self):
        from django.test import override_settings
        from django.urls import reverse
        from selfie_search.services.gallery_queue import run_once

        face = self.make_eligible_embedding(
            event=self.event, photo_id="public-queue", vector=[1.0] + [0.0] * 511
        )
        with override_settings(
            STORAGES={
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
            }
        ):
            response = self.client.post(
                reverse(
                    "selfie_search:submit_gallery_face",
                    kwargs={
                        "event_slug": self.event.slug,
                        "photo_id": face.detection.attempt.photo_id,
                        "detection_id": face.detection_id,
                    },
                )
            )
            self.assertEqual(response.status_code, 302)
            search = SelfieSearch.objects.get()
            self.assertEqual(search.status, "queued")
            self.assertTrue(run_once())
            search.refresh_from_db()
            self.assertEqual(search.status, "ready")
            result = self.client.get(response.url)
            self.assertContains(result, "Возможные совпадения")

    def test_transient_failure_retries_after_delay_then_finishes(self):
        from unittest.mock import patch

        from selfie_search.services.gallery_queue import claim, run_once
        from selfie_search.services.submission import GallerySearchFailed

        search = self.create_search()
        with patch(
            "selfie_search.services.gallery_queue.process_gallery_photo_search",
            side_effect=GallerySearchFailed,
        ):
            self.assertTrue(run_once())
        search.refresh_from_db()
        self.assertEqual(search.status, "queued")
        self.assertEqual(search.gallery_attempt_count, 1)
        self.assertIsNone(claim())
        SelfieSearch.objects.filter(pk=search.pk).update(gallery_available_at=timezone.now())
        self.assertTrue(run_once())
        search.refresh_from_db()
        self.assertEqual(search.status, "ready")
        self.assertEqual(search.gallery_attempt_count, 2)

    def test_live_transient_failures_stop_after_three_attempts(self):
        from unittest.mock import patch

        from selfie_search.services.gallery_queue import run_once
        from selfie_search.services.submission import GallerySearchFailed

        search = self.create_search()
        for attempt in range(1, 4):
            with patch(
                "selfie_search.services.gallery_queue.process_gallery_photo_search",
                side_effect=GallerySearchFailed,
            ):
                self.assertTrue(run_once())
            search.refresh_from_db()
            self.assertEqual(search.gallery_attempt_count, attempt)
            self.assertEqual(search.status, "failed" if attempt == 3 else "queued")
            SelfieSearch.objects.filter(pk=search.pk).update(gallery_available_at=timezone.now())
        self.assertFalse(run_once())

    def test_startup_database_retry_recovers_before_any_claim(self):
        from unittest.mock import patch

        from django.core.management import call_command
        from django.db import OperationalError
        from selfie_search.services.gallery_queue import recover_processing

        search = self.create_search()
        SelfieSearch.objects.filter(pk=search.pk).update(
            status="processing", gallery_attempt_count=3
        )
        events = []

        def recover():
            events.append("recover")
            if len(events) == 1:
                raise OperationalError("startup unavailable")
            return recover_processing()

        class ObservedClaim(Exception):
            pass

        def observe_claim():
            events.append("claim")
            search.refresh_from_db()
            self.assertEqual(search.status, "queued")
            self.assertEqual(search.gallery_attempt_count, 0)
            raise ObservedClaim()

        with (
            patch(
                "selfie_search.management.commands.run_gallery_search_worker.recover_processing",
                side_effect=recover,
            ),
            patch(
                "selfie_search.management.commands.run_gallery_search_worker.run_once",
                side_effect=observe_claim,
            ),
            patch(
                "selfie_search.management.commands.run_gallery_search_worker.time.sleep"
            ) as sleep,
            patch("selfie_search.management.commands.run_gallery_search_worker.connection.close"),
            patch("selfie_search.management.commands.run_gallery_search_worker.signal.signal"),
            patch("selfie_search.management.commands.run_gallery_search_worker.signal.alarm"),
            self.assertRaises(ObservedClaim),
        ):
            call_command("run_gallery_search_worker")
        self.assertEqual(events, ["recover", "recover", "claim"])
        sleep.assert_called_once_with(2)

    make_photo = fixtures.GalleryPhotoSubmissionTests.make_photo
    publish_watermark = fixtures.GalleryPhotoSubmissionTests.publish_watermark

    def test_accepted_paid_search_computes_independently_of_presentation_gate(self):
        from feature_flags.registry import PAID_EVENTS, PAID_WATERMARKED_PREVIEWS
        from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON, FEATURE_FLAG_STAFF
        from feature_flags.testing import override_feature_flags
        from picflow.models import Photo
        from selfie_search.services.gallery_queue import run_once
        from selfie_search.services.submission import submit_gallery_photo_search

        cases = [
            (FEATURE_FLAG_ON, False, "ready"),
            (FEATURE_FLAG_STAFF, True, "ready"),
            (FEATURE_FLAG_STAFF, False, "ready"),
            (FEATURE_FLAG_OFF, True, "ready"),
        ]
        for index, (state, staff, expected) in enumerate(cases):
            with self.subTest(state=state, staff=staff):
                event = self.make_event(f"paid-{index}", "paid")
                self.user.is_staff = staff
                photo = self.make_photo(event=event, photo_id=f"paid-{index}")
                photo.processing_generation = (
                    Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
                )
                photo.gallery_media_policy = Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
                photo.save()
                face = self.make_eligible_embedding(
                    event=event, photo_id=photo.pk, photo=photo, vector=[1.0] + [0.0] * 511
                )
                self.publish_watermark(photo)
                with override_feature_flags({PAID_EVENTS: FEATURE_FLAG_ON}):
                    search = submit_gallery_photo_search(
                        event=event,
                        photo=photo,
                        detection_id=face.detection_id,
                        user=self.user,
                        paid_watermarked_previews_enabled=True,
                    ).search
                with override_feature_flags({PAID_WATERMARKED_PREVIEWS: state}):
                    self.assertTrue(run_once())
                search.refresh_from_db()
                self.assertEqual(search.status, expected)

    def test_attempt_watchdog_kills_process_instead_of_continuing(self):
        import os
        import subprocess
        import sys
        from pathlib import Path

        backend = Path(__file__).resolve().parents[2]
        code = (
            "import django; django.setup(); "
            "from selfie_search.management.commands.run_gallery_search_worker "
            "import attempt_timeout; "
            'attempt_timeout(0, None); raise RuntimeError("continued after timeout")'
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONPATH": str(backend)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 124, result.stderr)


class GalleryWorkerRestartTests(TransactionTestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding
    create_search = GalleryQueueTests.create_search

    @contextmanager
    def worker(self, code=None):
        import os
        import signal
        import subprocess
        import sys
        from pathlib import Path

        from django.db import connection

        env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "DB_NAME": str(connection.settings_dict["NAME"]),
        }
        code = code or (
            "import django; django.setup(); from django.core.management import call_command; "
            'call_command("run_gallery_search_worker")'
        )
        with subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as worker:
            try:
                yield worker
            finally:
                if worker.poll() is None:
                    worker.send_signal(signal.SIGTERM)
                    try:
                        worker.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        worker.wait(timeout=3)

    def wait_for_search(self, search, worker):
        import time

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            search.refresh_from_db()
            if search.status == "ready":
                return
            if worker.poll() is not None:
                self.fail(worker.stderr.read())
            time.sleep(0.05)
        self.fail(f"search did not finish: {search.status}")

    def test_each_start_immediately_reprocesses_fresh_or_exhausted_processing(self):
        for attempts in (0, 3, 99):
            with self.subTest(attempts=attempts):
                search = self.create_search()
                SelfieSearch.objects.filter(pk=search.pk).update(
                    status="processing",
                    gallery_attempt_count=attempts,
                    gallery_available_at=timezone.now() + timedelta(days=1),
                )
                with self.worker() as worker:
                    self.wait_for_search(search, worker)
                    self.assertIsNone(worker.poll())
                    self.assertEqual(search.gallery_attempt_count, 1)
                    self.assertTrue(
                        search.results.filter(
                            photo_id=search.configuration["query_source"]["photo_id"]
                        ).exists()
                    )
                self.assertEqual(worker.returncode, 0)

    def test_fresh_worker_allows_120s_ranking_and_restores_30s_for_publication(self):
        import json

        search = self.create_search()
        code = """
import django
import json
import signal
from unittest.mock import patch
django.setup()
from django.core.management import call_command
from django.db import connection
from selfie_search.services import submission
rank = submission.rank_selected_direct
persist = submission._persist_gallery_results

def timeout_seconds():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('statement_timeout')::interval")
        return cursor.fetchone()[0].total_seconds()

def observe_rank(*args, **kwargs):
    remaining = signal.getitimer(signal.ITIMER_REAL)[0]
    print(json.dumps(['ranking', timeout_seconds(), remaining]), flush=True)
    return rank(*args, **kwargs)

def observe_publication(*args, **kwargs):
    connect = connection.connection.info.get_parameters()['connect_timeout']
    print(json.dumps(['publication', timeout_seconds(), connect]), flush=True)
    return persist(*args, **kwargs)

with (
    patch('selfie_search.services.submission.rank_selected_direct', observe_rank),
    patch('selfie_search.services.submission._persist_gallery_results', observe_publication),
):
    call_command('run_gallery_search_worker')
"""
        with self.worker(code) as worker:
            self.wait_for_search(search, worker)
            assert worker.stdout is not None
            ranking = json.loads(worker.stdout.readline())
            publication = json.loads(worker.stdout.readline())
            self.assertEqual(ranking[:2], ["ranking", 120])
            self.assertGreater(ranking[2], 120)
            self.assertLessEqual(ranking[2], 150)
            self.assertEqual(publication, ["publication", 30, "5"])
            self.assertIsNone(worker.poll())
            self.assertEqual(search.results.count(), 1)
        self.assertEqual(worker.returncode, 0)

    def test_sigterm_stops_immediately_and_next_start_recovers_without_budget_wait(self):
        import select
        import signal
        import time

        from selfie_search.services.gallery_queue import run_once

        ready = self.create_search()
        self.assertTrue(run_once())
        ready_snapshot = SelfieSearch.objects.filter(pk=ready.pk).values().get()
        ready_results = list(ready.results.values())
        search = self.create_search()
        code = """
import django
import time
from unittest.mock import patch
django.setup()
from django.core.management import call_command
from selfie_search.services.submission import rank_selected_direct

def paused_rank(*args, **kwargs):
    print('RANKING', flush=True)
    time.sleep(60)
    return rank_selected_direct(*args, **kwargs)

with patch('selfie_search.services.submission.rank_selected_direct', paused_rank):
    call_command('run_gallery_search_worker')
"""
        with self.worker(code) as worker:
            assert worker.stdout is not None
            self.assertTrue(select.select([worker.stdout], [], [], 10)[0])
            self.assertEqual(worker.stdout.readline().strip(), "RANKING")
            search.refresh_from_db()
            self.assertEqual(search.status, "processing")
            self.assertEqual(search.results.count(), 0)
            started = time.monotonic()
            worker.send_signal(signal.SIGTERM)
            worker.wait(timeout=3)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(worker.returncode, 0)
        search.refresh_from_db()
        self.assertEqual(search.status, "processing")
        SelfieSearch.objects.filter(pk=search.pk).update(
            gallery_attempt_count=3,
            gallery_available_at=timezone.now() + timedelta(days=1),
        )
        with self.worker() as worker:
            self.wait_for_search(search, worker)
            self.assertEqual(search.gallery_attempt_count, 1)
            self.assertEqual(search.results.count(), 2)
        recovered = SelfieSearch.objects.filter(pk=search.pk).values().get()
        recovered_results = list(search.results.values())
        following = self.create_search()
        with self.worker() as worker:
            self.wait_for_search(following, worker)
            self.assertIsNone(worker.poll())
            self.assertEqual(SelfieSearch.objects.filter(pk=search.pk).values().get(), recovered)
            self.assertEqual(list(search.results.values()), recovered_results)
            self.assertEqual(
                SelfieSearch.objects.filter(pk=ready.pk).values().get(), ready_snapshot
            )
            self.assertEqual(list(ready.results.values()), ready_results)
