import re
from collections.abc import Callable

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseNotAllowed,
)
from django.urls import Resolver404, resolve
from django.utils.cache import patch_vary_headers

_PUBLIC_BEARER_PATH = re.compile(r"^/events/[^/]+/selfie-search/[^/]+(?:/|$)")
_COMMERCE_ORDER_BEARER_PATH = re.compile(r"^/orders/[^/]+/access/[^/]+/[^/]+(?:/|$)")
_ARCHIVE_BEARER_PATH = re.compile(r"^/archives/")
_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
_SANITIZED_BEARER_PATH = "/events/<event>/selfie-search/<bearer>/"
_SANITIZED_COMMERCE_ORDER_BEARER_PATH = "/orders/<order>/access/<bearer>/"
_SANITIZED_ARCHIVE_BEARER_PATH = "/archives/<bearer>/"


class PublicSelfieBearerProtectionMiddleware:
    """Protect bearer-result requests before Django's CSRF and error handlers."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        is_selfie_bearer = _PUBLIC_BEARER_PATH.match(request.path_info) is not None
        is_commerce_bearer = _COMMERCE_ORDER_BEARER_PATH.match(request.path_info) is not None
        is_archive_bearer = _ARCHIVE_BEARER_PATH.match(request.path_info) is not None
        if not (is_selfie_bearer or is_commerce_bearer or is_archive_bearer):
            return self.get_response(request)

        original_path = request.path
        original_path_info = request.META.get("PATH_INFO")
        original_path_info_attribute = request.path_info
        # URL resolution uses path_info. BaseHandler and CsrfViewMiddleware log path,
        # so redact only the latter before an inner handler can turn an exception into a 4xx/5xx.
        if is_selfie_bearer:
            sanitized_path = _SANITIZED_BEARER_PATH
            request._is_public_selfie_bearer_request = True
        elif is_commerce_bearer:
            sanitized_path = _SANITIZED_COMMERCE_ORDER_BEARER_PATH
            request._is_commerce_order_bearer_request = True
        else:
            sanitized_path = _SANITIZED_ARCHIVE_BEARER_PATH
            request._is_archive_bearer_request = True
        is_allowed_post = _is_allowed_bearer_post(request)
        if request.method not in _READ_ONLY_METHODS and not is_allowed_post:
            request.path = sanitized_path
            request.META["PATH_INFO"] = sanitized_path
            request.path_info = sanitized_path
            return _protect_bearer_response(HttpResponseNotAllowed(["GET", "HEAD"]))
        request.path = sanitized_path
        if not is_allowed_post:
            request.META["PATH_INFO"] = sanitized_path
        response = _protect_bearer_response(self.get_response(request))
        request.path = original_path
        request.META["PATH_INFO"] = original_path_info
        request.path_info = original_path_info_attribute
        return response

    def process_view(
        self,
        request: HttpRequest,
        _view_func: Callable[..., object],
        _view_args: list[object],
        _view_kwargs: dict[str, object],
    ) -> None:
        """Sanitize diagnostic path_info only after Django has resolved the bearer URL."""
        if getattr(request, "_is_public_selfie_bearer_request", False):
            request.path_info = _SANITIZED_BEARER_PATH
        elif getattr(request, "_is_commerce_order_bearer_request", False):
            request.path_info = _SANITIZED_COMMERCE_ORDER_BEARER_PATH
        elif getattr(request, "_is_archive_bearer_request", False):
            request.path_info = _SANITIZED_ARCHIVE_BEARER_PATH

    def process_exception(
        self, request: HttpRequest, _exception: Exception
    ) -> HttpResponseBase | None:
        if not (
            getattr(request, "_is_public_selfie_bearer_request", False)
            or getattr(request, "_is_commerce_order_bearer_request", False)
            or getattr(request, "_is_archive_bearer_request", False)
        ):
            return None
        return _protect_bearer_response(HttpResponse(status=500))


def _protect_bearer_response(response: HttpResponseBase) -> HttpResponseBase:
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    patch_vary_headers(response, ("Cookie",))
    if response.status_code >= 400:
        # The final BaseHandler log would otherwise duplicate an error response.
        # An inner exception log, if any, sees the redacted request.path above.
        response._has_been_logged = True
    return response


def _is_allowed_bearer_post(request: HttpRequest) -> bool:
    if request.method != "POST":
        return False
    try:
        return resolve(request.path_info).view_name in {
            "selfie_search:continue",
            "selfie_search:feedback_selfie",
            "commerce:grant_order_resend",
            "commerce:grant_order_archive",
            "selfie_search:result_archive",
        }
    except Resolver404:
        return False
