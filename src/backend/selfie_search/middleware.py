import re
from collections.abc import Callable

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseNotAllowed,
)
from django.urls import Resolver404, resolve

_PUBLIC_BEARER_PATH = re.compile(r"^/events/[^/]+/selfie-search/[^/]+(?:/|$)")
_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
_SANITIZED_BEARER_PATH = "/events/<event>/selfie-search/<bearer>/"


class PublicSelfieBearerProtectionMiddleware:
    """Protect bearer-result requests before Django's CSRF and error handlers."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if _PUBLIC_BEARER_PATH.match(request.path_info) is None:
            return self.get_response(request)

        original_path = request.path
        original_path_info = request.META.get("PATH_INFO")
        # URL resolution uses path_info. BaseHandler and CsrfViewMiddleware log path,
        # so redact only the latter before an inner handler can turn an exception into a 4xx/5xx.
        request._is_public_selfie_bearer_request = True
        is_feedback_post = _is_feedback_post(request)
        if request.method not in _READ_ONLY_METHODS and not is_feedback_post:
            request.path = _SANITIZED_BEARER_PATH
            request.META["PATH_INFO"] = _SANITIZED_BEARER_PATH
            return _protect_bearer_response(HttpResponseNotAllowed(["GET", "HEAD"]))
        request.path = _SANITIZED_BEARER_PATH
        if not is_feedback_post:
            request.META["PATH_INFO"] = _SANITIZED_BEARER_PATH
        response = _protect_bearer_response(self.get_response(request))
        request.path = original_path
        request.META["PATH_INFO"] = original_path_info
        return response

    def process_exception(
        self, request: HttpRequest, _exception: Exception
    ) -> HttpResponseBase | None:
        if not getattr(request, "_is_public_selfie_bearer_request", False):
            return None
        return _protect_bearer_response(HttpResponse(status=500))


def _protect_bearer_response(response: HttpResponseBase) -> HttpResponseBase:
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    if response.status_code >= 400:
        # The final BaseHandler log would otherwise duplicate an error response.
        # An inner exception log, if any, sees the redacted request.path above.
        response._has_been_logged = True
    return response


def _is_feedback_post(request: HttpRequest) -> bool:
    if request.method != "POST":
        return False
    try:
        return resolve(request.path_info).view_name == "selfie_search:feedback"
    except Resolver404:
        return False
