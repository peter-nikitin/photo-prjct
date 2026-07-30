import time

from django.conf import settings
from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()
_LABEL_NAMES = ("environment", "route", "method", "status_class")
_ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

HTTP_REQUESTS = Counter(
    "findme_http_requests",
    "HTTP requests by route, method, and response status class.",
    _LABEL_NAMES,
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "findme_http_request_duration_seconds",
    "HTTP request duration by route, method, and response status class.",
    _LABEL_NAMES,
    registry=REGISTRY,
)


class HttpMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/metrics/":
            return self.get_response(request)

        started_at = time.perf_counter()
        response = self.get_response(request)
        labels = {
            "environment": settings.MONITORING_ENVIRONMENT,
            "route": self._route_name(request),
            "method": self._method_name(request),
            "status_class": f"{response.status_code // 100}xx",
        }
        HTTP_REQUESTS.labels(**labels).inc()
        HTTP_REQUEST_DURATION.labels(**labels).observe(time.perf_counter() - started_at)
        return response

    @staticmethod
    def _route_name(request) -> str:
        if request.resolver_match is None:
            return "unmatched"
        return request.resolver_match.view_name or "unmatched"

    @staticmethod
    def _method_name(request) -> str:
        if request.method in _ALLOWED_HTTP_METHODS:
            return request.method
        return "other"
