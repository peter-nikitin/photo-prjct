"""Authentication for the private, machine-only processing API."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from secrets import compare_digest
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse


def has_worker_token(request: HttpRequest) -> bool:
    """Return whether one exact configured bearer credential authorizes this request.

    Disabled or unconfigured deployments deliberately use the same response as a bad credential,
    so this endpoint never confirms feature or token configuration to an unauthenticated caller.
    """
    configured = settings.PHOTO_PROCESSING_WORKER_TOKEN
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    supplied = header[len(prefix) :] if header.startswith(prefix) else ""
    valid_shape = bool(supplied) and " " not in supplied
    # Keep the denial path's comparison operation independent of configuration and header shape.
    # A fixed dummy also avoids passing an empty secret to a timing-sensitive branch.
    compared = compare_digest(configured or "!worker-token-unconfigured!", supplied)
    return bool(settings.PHOTO_PROCESSING_ENABLED and configured and valid_shape and compared)


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
