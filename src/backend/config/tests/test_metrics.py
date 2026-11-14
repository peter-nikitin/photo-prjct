import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import path
from prometheus_client import REGISTRY

BACKEND_DIR = Path(__file__).resolve().parents[2]


def named_ok(request):  # noqa: ARG001
    return HttpResponse("ok")


def named_error(request):  # noqa: ARG001
    return HttpResponse("storage object 9f2a2f9e is unavailable", status=503)


def dynamic_photo(request, slug: str, photo_id: str, variant: str):  # noqa: ARG001
    return HttpResponse("photo")


urlpatterns = [
    path("test-ok/", named_ok, name="test_ok"),
    path("test-error/", named_error, name="test_error"),
    path(
        "events/<str:slug>/photos/<str:photo_id>/media/<str:variant>/",
        dynamic_photo,
        name="dynamic_photo",
    ),
]


@override_settings(ROOT_URLCONF=__name__)
class HttpMetricsMiddlewareTests(SimpleTestCase):
    def exposition(self) -> str:
        from prometheus_client import generate_latest

        return generate_latest(REGISTRY).decode()

    def test_records_named_200_route_with_only_bounded_labels(self) -> None:
        response = self.client.get("/test-ok/")

        self.assertEqual(response.status_code, 200)
        exposition = self.exposition()
        self.assertIn(
            'findme_http_requests_total{method="GET",route="test_ok",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertIn(
            'findme_http_request_duration_seconds_count{method="GET",'
            'route="test_ok",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertNotIn("path=", exposition)
        self.assertNotIn("query", exposition)

    def test_records_named_5xx_route_by_status_class(self) -> None:
        response = self.client.get("/test-error/")

        self.assertEqual(response.status_code, 503)
        self.assertIn(
            'findme_http_requests_total{method="GET",route="test_error",status_class="5xx"} 1.0',
            self.exposition(),
        )
        self.assertNotIn("storage object 9f2a2f9e is unavailable", self.exposition())

    def test_collapses_arbitrary_http_method_without_leaking_it(self) -> None:
        arbitrary_method = "METRIC_TOKEN_9F2A2F9E"

        response = self.client.generic(arbitrary_method, "/test-ok/")

        self.assertEqual(response.status_code, 200)
        exposition = self.exposition()
        self.assertIn(
            'findme_http_requests_total{method="other",route="test_ok",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertNotIn(arbitrary_method, exposition)

    def test_collapses_unmatched_path_without_request_data(self) -> None:
        secret_path = "/not-found/a7f7b7f7-789d-4b4f-a174-9f7c6b1aa123/?token=do-not-export"

        response = self.client.get(secret_path)

        self.assertEqual(response.status_code, 404)
        exposition = self.exposition()
        self.assertRegex(
            exposition,
            r'findme_http_requests_total\{method="GET",route="unmatched",'
            r'status_class="4xx"\} [1-9][0-9]*\.0',
        )
        for forbidden_value in (
            "not-found",
            "a7f7b7f7-789d-4b4f-a174-9f7c6b1aa123",
            "do-not-export",
        ):
            self.assertNotIn(forbidden_value, exposition)

    def test_normalizes_dynamic_route_without_slug_or_photo_identifier(self) -> None:
        response = self.client.get(
            "/events/private-run/photos/7bc7b7f7-789d-4b4f-a174-9f7c6b1aa123/media/preview-small/"
        )

        self.assertEqual(response.status_code, 200)
        exposition = self.exposition()
        self.assertIn(
            'findme_http_requests_total{method="GET",route="dynamic_photo",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertNotIn("private-run", exposition)
        self.assertNotIn("7bc7b7f7-789d-4b4f-a174-9f7c6b1aa123", exposition)


class MultiprocessMetricsTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        queue_patch = patch("config.metrics.collect_queue_samples", return_value={})
        queue_patch.start()
        self.addCleanup(queue_patch.stop)

    def test_web_vitals_scrape_exposes_zero_series_before_the_first_packet(self) -> None:
        scrape = """
            import django
            import psycopg
            from unittest.mock import patch

            django.setup()
            from config.metrics import generate_metrics

            with patch("config.metrics.psycopg.connect", side_effect=psycopg.OperationalError):
                print(generate_metrics().decode(), end="")
        """
        with tempfile.TemporaryDirectory() as metrics_directory:
            environment = {
                **os.environ,
                "PROMETHEUS_MULTIPROC_DIR": metrics_directory,
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DB_NAME": "app",
                "DB_USER": "app",
                "DB_PASSWORD": "app",
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "SECRET_KEY": "test",
                "ALLOWED_HOSTS": "testserver",
            }
            result = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(scrape)],
                cwd=BACKEND_DIR,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        for metric in ("lcp_seconds", "inp_seconds", "cls", "ttfb_seconds"):
            for page in ("catalog", "gallery", "legal", "photographer_login", "cart", "orders"):
                for viewport in ("narrow", "wide"):
                    expected = (
                        f"findme_web_vitals_{metric}_count"
                        f'{{page="{page}",viewport="{viewport}"}} 0.0'
                    )
                    self.assertIn(
                        expected,
                        result.stdout,
                    )

    @patch("config.metrics.collect_queue_samples")
    @patch("config.metrics.psycopg.connect")
    def test_queue_samples_have_bounded_labels_and_keep_successful_sources(self, connect, collect):
        from config.metrics import generate_metrics

        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.side_effect = [
            (1,),
            (0, 0, 0, 0, 0, 0, 0, 97),
            (0, 0, None, 0, 200_000_000),
            (0,),
            (0,),
            (0, 0),
        ]
        collect.return_value = {
            "gallery-search": (2, 310.0, 0.0, 1_000.0),
            "selfie": (0, 0.0, 950.0, 1_000.0),
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": directory}),
        ):
            output = generate_metrics().decode()
        self.assertIn('findme_queue_ready_count{queue="gallery-search"} 2', output)
        self.assertIn('findme_queue_oldest_ready_age_seconds{queue="gallery-search"} 310.0', output)
        self.assertIn('findme_queue_last_success_timestamp_seconds{queue="selfie"} 950.0', output)
        self.assertIn(
            'findme_queue_observed_timestamp_seconds{queue="gallery-search"} 1000.0', output
        )
        self.assertNotIn('queue="photo"', output)

    @patch("config.metrics.psycopg.connect")
    def test_database_check_uses_app_credentials_and_exports_fresh_success(self, connect) -> None:
        from config.metrics import generate_metrics

        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.side_effect = [
            (1,),
            (2, 3, 1, 1, 12.5, 8.25, 5.0, 97),
            (123, 456, 7200.0, 42_000_000, 200_000_000),
            (1024,),
            (0,),
            (0, 0),
        ]
        with (
            tempfile.TemporaryDirectory() as metrics_directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": metrics_directory}),
            override_settings(
                DATABASES={
                    "default": {
                        "NAME": "app",
                        "USER": "app_user",
                        "PASSWORD": "private-value",
                        "HOST": "db",
                        "PORT": "5432",
                    }
                }
            ),
        ):
            output = generate_metrics().decode()

        self.assertIn("findme_db_usable 1", output)
        self.assertIn("findme_db_check_timestamp_seconds", output)
        self.assertIn('findme_db_sessions{state="active"} 2', output)
        self.assertIn('findme_db_sessions{state="idle"} 3', output)
        self.assertIn('findme_db_sessions{state="idle_in_transaction"} 1', output)
        self.assertIn("findme_db_blocked_sessions 1", output)
        self.assertIn("findme_db_lock_wait_oldest_seconds 5.0", output)
        self.assertIn("findme_db_usable_connections 97", output)
        self.assertIn("findme_db_dead_tuples 123", output)
        self.assertIn("findme_db_live_tuples 456", output)
        self.assertIn("findme_db_oldest_vacuum_seconds 7200.0", output)
        self.assertIn("findme_db_max_relation_xid_age 42000000", output)
        self.assertIn("findme_db_autovacuum_freeze_max_age 200000000", output)
        self.assertIn("# TYPE findme_db_wal_bytes_total counter", output)
        self.assertIn("findme_db_wal_bytes_total 1024", output)
        self.assertNotIn("private-value", output)
        self.assertNotIn("app_user", output)
        self.assertNotIn("SELECT 1", output)
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 2)
        self.assertIn("statement_timeout=2000", connect.call_args.kwargs["options"])
        self.assertEqual(connection.execute.call_args_list[0].args, ("SELECT 1",))

    @patch("config.metrics.psycopg.connect")
    def test_optional_activity_query_failure_does_not_turn_successful_sql_check_to_zero(
        self, connect
    ) -> None:
        import psycopg

        from config.metrics import generate_metrics

        connection = connect.return_value.__enter__.return_value
        connection.execute.side_effect = [
            SimpleNamespace(fetchone=lambda: (1,)),
            psycopg.OperationalError("sensitive activity detail"),
            SimpleNamespace(fetchone=lambda: (0,)),
            SimpleNamespace(fetchone=lambda: (0, 0)),
            SimpleNamespace(fetchall=list),
        ]
        with (
            tempfile.TemporaryDirectory() as metrics_directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": metrics_directory}),
        ):
            output = generate_metrics().decode()

        self.assertIn("findme_db_usable 1", output)
        self.assertNotIn("findme_db_sessions", output)
        self.assertNotIn("sensitive activity detail", output)

    @patch("config.metrics.psycopg.connect")
    def test_wal_counter_failure_is_missing_without_failing_sql_check(self, connect) -> None:
        import psycopg

        from config.metrics import generate_metrics

        connection = connect.return_value.__enter__.return_value
        connection.execute.side_effect = [
            SimpleNamespace(fetchone=lambda: (1,)),
            SimpleNamespace(fetchone=lambda: (0, 0, 0, 0, 0, 0, 0, 97)),
            SimpleNamespace(fetchone=lambda: (0, 0, None, 0, 200_000_000)),
            psycopg.OperationalError("private WAL detail"),
            SimpleNamespace(fetchone=lambda: (0,)),
            SimpleNamespace(fetchone=lambda: (0, 0)),
            SimpleNamespace(fetchall=list),
        ]
        with (
            tempfile.TemporaryDirectory() as metrics_directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": metrics_directory}),
        ):
            output = generate_metrics().decode()

        self.assertIn("findme_db_usable 1", output)
        self.assertNotIn("findme_db_wal_bytes_total", output)
        self.assertNotIn("private WAL detail", output)

    @patch("config.metrics.psycopg.connect")
    def test_database_connection_failure_exports_zero_without_error_text(self, connect) -> None:
        import psycopg

        from config.metrics import generate_metrics

        connect.side_effect = psycopg.OperationalError("secret database detail")
        with (
            tempfile.TemporaryDirectory() as metrics_directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": metrics_directory}),
        ):
            output = generate_metrics().decode()

        self.assertIn("findme_db_usable 0", output)
        self.assertIn("findme_db_check_timestamp_seconds", output)
        self.assertNotIn("secret database detail", output)
        self.assertNotIn("findme_gallery_search_", output)

    @patch("config.metrics.psycopg.connect")
    def test_gallery_depth_failure_omits_only_its_own_series(self, connect):
        import psycopg

        from config.metrics import generate_metrics

        connection = connect.return_value.__enter__.return_value
        connection.execute.side_effect = [
            SimpleNamespace(fetchone=lambda: (1,)),
            SimpleNamespace(fetchone=lambda: (0, 0, 0, 0, 0, 0, 0, 97)),
            SimpleNamespace(fetchone=lambda: (0, 0, None, 0, 200_000_000)),
            SimpleNamespace(fetchone=lambda: (1024,)),
            psycopg.OperationalError("private search details"),
            SimpleNamespace(fetchone=lambda: (0, 0)),
            SimpleNamespace(fetchall=list),
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": directory}),
        ):
            output = generate_metrics().decode()
        self.assertIn("findme_db_usable 1", output)
        self.assertIn("findme_db_wal_bytes_total 1024", output)
        self.assertNotIn("findme_gallery_search_", output)
        self.assertNotIn("private search details", output)
        self.assertIn('findme_selfie_search_queue_depth{stage="recognition"} 0', output)

    def test_aggregates_metrics_recorded_by_multiple_gunicorn_processes(self) -> None:
        """A scrape must include observations from every Gunicorn worker process."""
        worker = """
            from config.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

            labels = {                "route": "health",
                "method": "GET",
                "status_class": "2xx",
            }
            HTTP_REQUESTS.labels(**labels).inc()
            HTTP_REQUEST_DURATION.labels(**labels).observe(0.5)
        """
        scrape = """
            import django
            from django.test import Client

            django.setup()

            response = Client().get("/metrics/")
            if response.status_code != 200:
                raise RuntimeError(f"unexpected status: {response.status_code}")
            if response["Content-Type"] != "text/plain; version=1.0.0; charset=utf-8":
                raise RuntimeError(f"unexpected content type: {response['Content-Type']}")
            print(response.content.decode(), end="")
        """

        with tempfile.TemporaryDirectory() as metrics_directory:
            environment = {
                **os.environ,
                "PROMETHEUS_MULTIPROC_DIR": metrics_directory,
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DB_NAME": "app",
                "DB_USER": "app",
                "DB_PASSWORD": "app",
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "SECRET_KEY": "test",
                "ALLOWED_HOSTS": "testserver",
            }
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "-c", textwrap.dedent(worker)],
                    cwd=BACKEND_DIR,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            result = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(scrape)],
                cwd=BACKEND_DIR,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            'findme_http_requests_total{method="GET",route="health",status_class="2xx"} 2.0',
            result.stdout,
        )
        self.assertIn(
            'findme_http_request_duration_seconds_count{method="GET",'
            'route="health",status_class="2xx"} 2.0',
            result.stdout,
        )
        self.assertIn(
            'findme_http_request_duration_seconds_sum{method="GET",'
            'route="health",status_class="2xx"} 1.0',
            result.stdout,
        )
        self.assertIn("findme_accepted_previews_total 0.0", result.stdout)
        self.assertNotIn('route="metrics"', result.stdout)

    def test_accepted_preview_counter_aggregates_committed_observations(self) -> None:
        worker = """
            from config.metrics import observe_accepted_preview

            observe_accepted_preview()
        """
        scrape = """
            import django
            from django.test import Client

            django.setup()
            response = Client().get("/metrics/")
            print(response.content.decode(), end="")
        """

        with tempfile.TemporaryDirectory() as metrics_directory:
            environment = {
                **os.environ,
                "PROMETHEUS_MULTIPROC_DIR": metrics_directory,
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DB_NAME": "app",
                "DB_USER": "app",
                "DB_PASSWORD": "app",
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "SECRET_KEY": "test",
                "ALLOWED_HOSTS": "testserver",
            }
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "-c", textwrap.dedent(worker)],
                    cwd=BACKEND_DIR,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(scrape)],
                cwd=BACKEND_DIR,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("findme_accepted_previews_total 2.0", result.stdout)


class GunicornMetricsLifecycleTests(SimpleTestCase):
    def test_child_exit_is_safe_when_multiprocess_metrics_are_unavailable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            from config.gunicorn import child_exit

            child_exit(None, SimpleNamespace(pid=123))

    def test_child_exit_removes_live_metrics_for_the_exiting_worker(self) -> None:
        with tempfile.TemporaryDirectory() as metrics_directory:
            stale_metric = Path(metrics_directory) / "gauge_livesum_123.db"
            stale_metric.touch()
            with patch.dict(
                os.environ, {"PROMETHEUS_MULTIPROC_DIR": metrics_directory}, clear=False
            ):
                from config.gunicorn import child_exit

                child_exit(None, SimpleNamespace(pid=123))

            self.assertFalse(stale_metric.exists())


class GallerySearchMetricsTests(TransactionTestCase):
    def setUp(self):
        from picflow.models import Event

        self.event = Event.objects.create(
            name="Private metrics event",
            slug="private-metrics-event",
            city="Moscow",
            start_date="2026-11-10",
            end_date="2026-11-10",
        )

    def search(
        self, *, status="queued", processor="gallery_photo_query", started=None, terminal=None
    ):
        from uuid import uuid4

        from selfie_search.models import FaceSearchTask, SelfieSearch

        gallery = processor == "gallery_photo_query"
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=uuid4().hex * 2,
            status=status,
            stage="matching" if gallery else "recognition",
            temporary_object_key="",
            configuration={"processor": processor},
            matching_started_at=started,
            terminal_at=terminal,
        )
        if gallery and status == "queued":
            FaceSearchTask.objects.create(
                search=search,
                event=self.event,
                source="gallery",
                embedding_model="adaface-ir18-webface4m",
                vector=[1.0] + [0.0] * 511,
            )
        return search

    def exposition(self):
        from config.metrics import generate_metrics

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": directory}),
        ):
            return generate_metrics().decode()

    def samples(self):
        from prometheus_client.parser import text_string_to_metric_families

        return {
            (sample.name, tuple(sorted(sample.labels.items()))): sample.value
            for family in text_string_to_metric_families(self.exposition())
            for sample in family.samples
            if sample.name.startswith("findme_gallery_search_")
        }

    def test_total_search_p75_uses_recent_successes_per_source(self):
        from datetime import timedelta

        from django.utils import timezone
        from prometheus_client.parser import text_string_to_metric_families
        from selfie_search.models import SelfieSearch

        now = timezone.now()
        metric = "findme_face_search_total_duration_p75_seconds"

        def p75_by_source():
            return {
                sample.labels["source"]: sample.value
                for family in text_string_to_metric_families(self.exposition())
                for sample in family.samples
                if sample.name == metric
            }

        def search(processor, duration, *, terminal=now, status="ready"):
            row = self.search(processor=processor, status=status, terminal=terminal)
            SelfieSearch.objects.filter(pk=row.pk).update(
                created_at=terminal - timedelta(seconds=duration)
            )

        self.assertEqual(p75_by_source(), {})
        for duration in (10, 20, 30, 50):
            search("gallery_photo_query", duration)
        search("gallery_photo_query", 500, status="failed")
        search("gallery_photo_query", 600, terminal=now - timedelta(hours=25))
        search("selfie_query", 80)

        self.assertEqual(p75_by_source(), {"gallery": 35, "selfie": 80})

    def test_gallery_queue_depth_includes_future_retries_and_excludes_other_searches(self):
        from datetime import timedelta

        from django.utils import timezone
        from selfie_search.models import FaceSearchTask

        now = timezone.now()
        self.search(status="ready", terminal=now)
        self.search(status="processing", started=now)
        selfie = self.search(processor="selfie_query")
        FaceSearchTask.objects.create(
            search=selfie,
            event=self.event,
            source="selfie",
            embedding_model="adaface-ir18-webface4m",
            vector=[1.0] + [0.0] * 511,
        )
        delayed = self.search()
        FaceSearchTask.objects.filter(search=delayed).update(available_at=now + timedelta(days=1))
        self.assertEqual(self.samples()[("findme_gallery_search_queue_depth", ())], 1)
        self.assertNotIn("private-metrics-event", self.exposition())

    def test_ready_gallery_observation_excludes_future_retry_and_uses_durable_success(self):
        from datetime import timedelta

        from django.utils import timezone
        from selfie_search.models import FaceSearchTask

        from config.queue_metrics import collect_queue_samples

        now = timezone.now()
        ready = self.search()
        FaceSearchTask.objects.filter(search=ready).update(available_at=now - timedelta(minutes=7))
        future = self.search()
        FaceSearchTask.objects.filter(search=future).update(available_at=now + timedelta(minutes=5))
        done = self.search(status="ready", terminal=now - timedelta(minutes=1))
        sample = collect_queue_samples()["gallery-search"]
        self.assertEqual(sample[0], 1)
        self.assertGreaterEqual(sample[1], 420)
        self.assertAlmostEqual(sample[2], done.terminal_at.timestamp(), delta=0.1)

    def test_selfie_depth_counts_each_waiting_stage_after_task_cleanup(self):
        from datetime import timedelta

        from django.utils import timezone
        from prometheus_client.parser import text_string_to_metric_families
        from selfie_search.models import FaceSearchTask, SelfieSearchJob
        from selfie_search.services.face_search_queue import prune_terminal_tasks

        terminal = timezone.now()
        ready = self.search(status="ready", processor="selfie_query", terminal=terminal)
        pending_recognition = self.search(processor="selfie_query")
        SelfieSearchJob.objects.create(
            search=pending_recognition,
            status="retry_wait",
            available_at=terminal + timedelta(days=1),
        )
        pending_matching = self.search(processor="selfie_query")
        FaceSearchTask.objects.bulk_create(
            [
                FaceSearchTask(
                    search=search,
                    event=self.event,
                    source="selfie",
                    embedding_model="adaface-ir18-webface4m",
                    vector=[1.0] + [0.0] * 511,
                    status=status,
                )
                for search, status in [(ready, "terminal"), (pending_matching, "queued")]
            ]
        )
        self.assertEqual(prune_terminal_tasks(batch_size=10), 1)

        samples = {
            (sample.name, sample.labels["stage"], sample.labels.get("phase")): sample.value
            for family in text_string_to_metric_families(self.exposition())
            for sample in family.samples
            if sample.name.startswith("findme_selfie_search_")
        }
        self.assertEqual(
            samples,
            {
                ("findme_selfie_search_queue_depth", "recognition", None): 1,
                ("findme_selfie_search_queue_depth", "matching", None): 1,
            },
        )

    def test_import_observation_respects_owner_permission_and_feature_gate(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from feature_flags.models import FeatureFlag
        from ingestion.models import ImportBatch

        from config.queue_metrics import collect_queue_samples

        owner = get_user_model().objects.create_user(username="metrics-import-owner")
        batch = ImportBatch.objects.create(
            owner=owner,
            event=self.event,
            submitted_source_key="public-folder",
            submission_key="metrics-import",
        )
        flag, _ = FeatureFlag.objects.update_or_create(
            key="yandex-disk-import",
            defaults={"description": "Import", "state": "on"},
        )
        self.assertEqual(collect_queue_samples()["import"][0], 0)
        owner.user_permissions.add(Permission.objects.get(codename="upload_photos"))
        self.assertEqual(collect_queue_samples()["import"][0], 1)
        flag.state = "off"
        flag.save(update_fields=["state"])
        self.assertEqual(collect_queue_samples()["import"][0], 0)
        self.assertEqual(batch.status, "queued")

    def test_failed_queue_observation_omits_only_that_queue(self):
        from django.db import DatabaseError

        from config.queue_metrics import collect_queue_samples

        with patch("config.queue_metrics._gallery", side_effect=DatabaseError("private")):
            samples = collect_queue_samples()
        self.assertNotIn("gallery-search", samples)
        self.assertIn("commerce", samples)
        self.assertEqual(samples["commerce"][:3], (0, 0.0, 0.0))

    def test_observation_restores_outer_transaction_statement_timeout(self):
        from django.db import connection, transaction

        from config.queue_metrics import collect_queue_samples

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 7000")
                cursor.execute("SHOW statement_timeout")
                before = cursor.fetchone()[0]
            collect_queue_samples()
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                after = cursor.fetchone()[0]
        self.assertEqual(before, after)

    @override_settings(PHOTO_PROCESSING_ENABLED=False)
    def test_disabled_processing_queues_publish_explicit_nonrunnable_zero(self):
        from config.queue_metrics import collect_queue_samples

        samples = collect_queue_samples()
        self.assertEqual(samples["selfie"][:3], (0, 0.0, 0.0))
        self.assertEqual(samples["photo"][:3], (0, 0.0, 0.0))

    @override_settings(
        PHOTO_PROCESSING_ENABLED=True,
        PHOTO_PROCESSING_FACE_ENABLED=False,
        PHOTO_PROCESSING_PREVIEW_ENABLED=False,
    )
    def test_existing_photo_jobs_remain_ready_when_enrollment_flags_turn_off(self):
        from datetime import timedelta

        from django.utils import timezone
        from picflow.models import Photo
        from processing.models import EventProcessingRun, PhotoProcessingState, ProcessingJob

        from config.queue_metrics import collect_queue_samples

        last_success = timezone.now() - timedelta(minutes=5)
        for index, (contract, processor, version) in enumerate(
            ((3, "face_embedding", 5), (2, "generate_preview", 1))
        ):
            photo = Photo.objects.create(
                id=f"metrics-photo-{index}", event=self.event, src="https://example.test/photo.jpg"
            )
            run = EventProcessingRun.objects.create(
                event=self.event,
                contract_version=contract,
                processor_type=processor,
                processor_version=version,
                configuration_hash="a" * 64,
                status="collecting",
            )
            job = ProcessingJob.objects.create(
                event=self.event,
                photo=photo,
                run=run,
                contract_version=contract,
                processor_type=processor,
                processor_version=version,
                configuration_hash="a" * 64,
                available_at=timezone.now() - timedelta(minutes=10),
            )
            PhotoProcessingState.objects.create(
                photo=photo,
                processor_type=processor,
                status="queued",
                current_run=run,
                current_job=job,
            )
            processed_photo = Photo.objects.create(
                id=f"metrics-processed-{index}", event=self.event, src="https://example.test/ok.jpg"
            )
            ProcessingJob.objects.create(
                event=self.event,
                photo=processed_photo,
                run=run,
                contract_version=contract,
                processor_type=processor,
                processor_version=version,
                configuration_hash="a" * 64,
                status="succeeded",
                completed_at=last_success - timedelta(minutes=index),
            )
        sample = collect_queue_samples()["photo"]
        self.assertEqual(sample[0], 2)
        self.assertEqual(sample[2], last_success.timestamp())

    def test_expired_import_file_lease_is_ready_for_immediate_reclaim(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from django.utils import timezone
        from feature_flags.models import FeatureFlag
        from ingestion.models import ImportAttempt, ImportBatch, ImportItem
        from ingestion.services.imports import claim_import_work

        from config.queue_metrics import collect_queue_samples

        now = timezone.now()
        owner = get_user_model().objects.create_user(username="metrics-reclaim-owner")
        owner.user_permissions.add(Permission.objects.get(codename="upload_photos"))
        FeatureFlag.objects.update_or_create(
            key="yandex-disk-import", defaults={"description": "Import", "state": "on"}
        )
        for index, (status, expires_at, attempts) in enumerate(
            (
                ("claimed", now - timedelta(minutes=1), 1),
                ("uploading", now + timedelta(minutes=1), 1),
                ("claimed", now - timedelta(minutes=1), 4),
            )
        ):
            batch = ImportBatch.objects.create(
                owner=owner,
                event=self.event,
                submitted_source_key=f"folder-{index}",
                submission_key=f"reclaim-{index}",
                status="transferring",
                manifest_complete=True,
                last_activity_at=now - timedelta(minutes=20),
            )
            item = ImportItem.objects.create(
                batch=batch,
                source_path=f"photo-{index}.jpg",
                filename=f"photo-{index}.jpg",
                byte_size=1,
                status=status,
                download_attempts=attempts,
            )
            if index == 0:
                reclaimable_item_id = item.id
            ImportAttempt.objects.create(
                batch=batch,
                item=item,
                kind="file",
                status="active",
                claimed_at=now - timedelta(minutes=5),
                heartbeat_at=now - timedelta(minutes=5),
                lease_expires_at=expires_at,
            )
        sample = collect_queue_samples()["import"]
        self.assertEqual(sample[0], 1)
        self.assertGreaterEqual(sample[1], 60)
        self.assertLess(sample[1], 120)
        claim = claim_import_work(now=now)
        self.assertEqual((claim.kind, claim.item_id), ("file", reclaimable_item_id))
