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


@override_settings(ROOT_URLCONF=__name__, MONITORING_ENVIRONMENT="test")
class HttpMetricsMiddlewareTests(SimpleTestCase):
    def exposition(self) -> str:
        from prometheus_client import generate_latest

        return generate_latest(REGISTRY).decode()

    def test_records_named_200_route_with_only_bounded_labels(self) -> None:
        response = self.client.get("/test-ok/")

        self.assertEqual(response.status_code, 200)
        exposition = self.exposition()
        self.assertIn(
            'findme_http_requests_total{environment="test",method="GET",'
            'route="test_ok",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertIn(
            'findme_http_request_duration_seconds_count{environment="test",method="GET",'
            'route="test_ok",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertNotIn("path=", exposition)
        self.assertNotIn("query", exposition)

    def test_records_named_5xx_route_by_status_class(self) -> None:
        response = self.client.get("/test-error/")

        self.assertEqual(response.status_code, 503)
        self.assertIn(
            'findme_http_requests_total{environment="test",method="GET",'
            'route="test_error",status_class="5xx"} 1.0',
            self.exposition(),
        )
        self.assertNotIn("storage object 9f2a2f9e is unavailable", self.exposition())

    def test_collapses_arbitrary_http_method_without_leaking_it(self) -> None:
        arbitrary_method = "METRIC_TOKEN_9F2A2F9E"

        response = self.client.generic(arbitrary_method, "/test-ok/")

        self.assertEqual(response.status_code, 200)
        exposition = self.exposition()
        self.assertIn(
            'findme_http_requests_total{environment="test",method="other",'
            'route="test_ok",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertNotIn(arbitrary_method, exposition)

    def test_collapses_unmatched_path_without_request_data(self) -> None:
        secret_path = "/not-found/a7f7b7f7-789d-4b4f-a174-9f7c6b1aa123/?token=do-not-export"

        response = self.client.get(secret_path)

        self.assertEqual(response.status_code, 404)
        exposition = self.exposition()
        self.assertIn(
            'findme_http_requests_total{environment="test",method="GET",'
            'route="unmatched",status_class="4xx"} 1.0',
            exposition,
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
            'findme_http_requests_total{environment="test",method="GET",'
            'route="dynamic_photo",status_class="2xx"} 1.0',
            exposition,
        )
        self.assertNotIn("private-run", exposition)
        self.assertNotIn("7bc7b7f7-789d-4b4f-a174-9f7c6b1aa123", exposition)


class MultiprocessMetricsTests(SimpleTestCase):
    def test_aggregates_metrics_recorded_by_multiple_gunicorn_processes(self) -> None:
        """A scrape must include observations from every Gunicorn worker process."""
        worker = """
            from config.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

            labels = {
                "environment": "staging",
                "route": "health",
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
            'findme_http_requests_total{environment="staging",method="GET",'
            'route="health",status_class="2xx"} 2.0',
            result.stdout,
        )
        self.assertIn(
            'findme_http_request_duration_seconds_count{environment="staging",method="GET",'
            'route="health",status_class="2xx"} 2.0',
            result.stdout,
        )
        self.assertIn(
            'findme_http_request_duration_seconds_sum{environment="staging",method="GET",'
            'route="health",status_class="2xx"} 1.0',
            result.stdout,
        )
        self.assertNotIn('route="metrics"', result.stdout)


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
