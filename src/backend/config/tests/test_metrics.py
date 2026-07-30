from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path, reverse

from config.metrics import REGISTRY


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
            "findme_http_requests_total{environment=\"test\",method=\"GET\","
            "route=\"test_ok\",status_class=\"2xx\"} 1.0",
            exposition,
        )
        self.assertIn(
            "findme_http_request_duration_seconds_count{environment=\"test\",method=\"GET\","
            "route=\"test_ok\",status_class=\"2xx\"} 1.0",
            exposition,
        )
        self.assertNotIn("path=", exposition)
        self.assertNotIn("query", exposition)

    def test_records_named_5xx_route_by_status_class(self) -> None:
        response = self.client.get("/test-error/")

        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "findme_http_requests_total{environment=\"test\",method=\"GET\","
            "route=\"test_error\",status_class=\"5xx\"} 1.0",
            self.exposition(),
        )
        self.assertNotIn("storage object 9f2a2f9e is unavailable", self.exposition())

    def test_collapses_arbitrary_http_method_without_leaking_it(self) -> None:
        arbitrary_method = "METRIC_TOKEN_9F2A2F9E"

        response = self.client.generic(arbitrary_method, "/test-ok/")

        self.assertEqual(response.status_code, 200)
        exposition = self.exposition()
        self.assertIn(
            "findme_http_requests_total{environment=\"test\",method=\"other\","
            "route=\"test_ok\",status_class=\"2xx\"} 1.0",
            exposition,
        )
        self.assertNotIn(arbitrary_method, exposition)

    def test_collapses_unmatched_path_without_request_data(self) -> None:
        secret_path = "/not-found/a7f7b7f7-789d-4b4f-a174-9f7c6b1aa123/?token=do-not-export"

        response = self.client.get(secret_path)

        self.assertEqual(response.status_code, 404)
        exposition = self.exposition()
        self.assertIn(
            "findme_http_requests_total{environment=\"test\",method=\"GET\","
            "route=\"unmatched\",status_class=\"4xx\"} 1.0",
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
            "findme_http_requests_total{environment=\"test\",method=\"GET\","
            "route=\"dynamic_photo\",status_class=\"2xx\"} 1.0",
            exposition,
        )
        self.assertNotIn("private-run", exposition)
        self.assertNotIn("7bc7b7f7-789d-4b4f-a174-9f7c6b1aa123", exposition)


class MetricsEndpointTests(SimpleTestCase):
    def test_returns_prometheus_content_without_observing_the_scrape(self) -> None:
        with patch("config.metrics.time.perf_counter", side_effect=(1.0, 1.5)):
            self.client.get(reverse("health"))

        response = self.client.get("/metrics/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; version=1.0.0; charset=utf-8")
        self.assertContains(response, "findme_http_requests_total")
        self.assertNotIn('route="metrics"', response.content.decode())
