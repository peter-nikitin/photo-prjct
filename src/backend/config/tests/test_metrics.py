import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
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
    @patch("config.metrics.psycopg.connect")
    def test_database_check_uses_app_credentials_and_exports_fresh_success(self, connect) -> None:
        from config.metrics import generate_metrics

        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.side_effect = [
            (1,),
            (2, 3, 1, 1, 12.5, 8.25, 5.0, 97),
            (123, 456, 7200.0, 42_000_000, 200_000_000),
            (1024,),
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
