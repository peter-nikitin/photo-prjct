"""Canonical diagnostic scrape, separate from application request metrics."""

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET
from prometheus_client import CONTENT_TYPE_LATEST

from processing.services.worker_pool_telemetry import generate_diagnostic_metrics


@require_GET
def metrics(request: HttpRequest) -> HttpResponse:  # noqa: ARG001
    response = HttpResponse(generate_diagnostic_metrics(), content_type=CONTENT_TYPE_LATEST)
    response["Cache-Control"] = "no-store"
    return response
