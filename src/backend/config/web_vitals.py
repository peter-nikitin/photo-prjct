"""Anonymous, bounded Web Vitals receiver."""

import json
import math

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

from config.metrics import (
    WEB_VITALS_CLS,
    WEB_VITALS_INP,
    WEB_VITALS_LCP,
    WEB_VITALS_PAGE_BY_ROUTE,
    WEB_VITALS_TTFB,
    WEB_VITALS_VIEWPORTS,
)

_OBSERVERS = {
    "LCP": WEB_VITALS_LCP,
    "INP": WEB_VITALS_INP,
    "CLS": WEB_VITALS_CLS,
    "TTFB": WEB_VITALS_TTFB,
}
_MAX_VALUES = {"LCP": 86_400_000, "INP": 86_400_000, "CLS": 10_000, "TTFB": 86_400_000}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON key")
    return value


def _valid_packet(packet: object) -> bool:
    if not isinstance(packet, dict) or set(packet) != {"page", "viewport", "metrics"}:
        return False
    if (
        packet["page"] not in WEB_VITALS_PAGE_BY_ROUTE.values()
        or packet["viewport"] not in WEB_VITALS_VIEWPORTS
    ):
        return False
    metrics = packet["metrics"]
    if not isinstance(metrics, dict) or not metrics or not set(metrics) <= _OBSERVERS.keys():
        return False
    return all(
        type(value) in (int, float) and 0 <= value <= _MAX_VALUES[name] and math.isfinite(value)
        for name, value in metrics.items()
    )


def observe_web_vitals(packet: dict) -> None:
    for metric, value in packet["metrics"].items():
        _OBSERVERS[metric].labels(page=packet["page"], viewport=packet["viewport"]).observe(
            value if metric == "CLS" else value / 1000
        )


@csrf_exempt
def web_vitals(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("HTTP_SEC_FETCH_SITE") != "same-origin":
        return HttpResponse(status=403)
    if request.content_type != "application/json":
        return HttpResponse(status=415)
    try:
        declared_size = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return HttpResponse(status=400)
    if declared_size < 0:
        return HttpResponse(status=400)
    if declared_size > 1024:
        return HttpResponse(status=413)
    body = request.body
    if len(body) > 1024:
        return HttpResponse(status=413)
    try:
        packet = json.loads(body, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return HttpResponse(status=400)
    if not _valid_packet(packet):
        return HttpResponse(status=400)
    observe_web_vitals(packet)
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response
