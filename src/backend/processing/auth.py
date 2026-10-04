"""Authentication for the private, machine-only processing API."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from secrets import compare_digest
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse


def local_worker_enabled() -> bool:
    """Local protocol fixtures are restricted to development without a coordinator."""
    return bool(settings.DEBUG and not settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED)


def worker_endpoint_enabled() -> bool:
    return bool(
        settings.PHOTO_PROCESSING_FLEET_TOKEN
        or (local_worker_enabled() and settings.PHOTO_PROCESSING_WORKER_TOKEN)
    )


def has_worker_token(request: HttpRequest) -> bool:
    """Return whether one exact configured bearer credential authorizes this request.

    Unconfigured deployments deliberately use the same response as a bad credential,
    so this endpoint never confirms token configuration to an unauthenticated caller.
    """
    local_token = settings.PHOTO_PROCESSING_WORKER_TOKEN
    fleet_token = settings.PHOTO_PROCESSING_FLEET_TOKEN
    marker = request.headers.get("X-FindMe-Worker-Transport", "")
    configured = fleet_token if marker == "private-tls" else local_token
    transport_valid = (marker == "private-tls" or (marker == "" and local_worker_enabled())) and (
        not local_worker_enabled()
        or not fleet_token
        or not compare_digest(local_token, fleet_token)
    )
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    supplied = header[len(prefix) :] if header.startswith(prefix) else ""
    valid_shape = bool(supplied) and " " not in supplied
    # Keep the denial path's comparison operation independent of configuration and header shape.
    # A fixed dummy also avoids passing an empty secret to a timing-sensitive branch.
    compared = compare_digest(configured or "!worker-token-unconfigured!", supplied)
    return bool(transport_valid and configured and valid_shape and compared)


def require_worker_token(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not has_worker_token(request):
            return JsonResponse(
                {"error": {"code": "worker_unauthorized", "message": "Unauthorized."}},
                status=401,
            )
        return view(request, *args, **kwargs)

    return wrapped
