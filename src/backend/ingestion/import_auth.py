"""Authentication for the dedicated private photo-import worker API."""

from __future__ import annotations

from secrets import compare_digest

from django.conf import settings
from django.http import HttpRequest


def has_import_worker_token(request: HttpRequest) -> bool:
    configured = settings.PHOTO_IMPORT_WORKER_TOKEN
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    supplied = header[len(prefix) :] if header.startswith(prefix) else ""
    valid_shape = (
        bool(supplied)
        and supplied.isascii()
        and not any(character.isspace() for character in supplied)
    )
    configured_bytes = (configured or "!import-worker-token-unconfigured!").encode()
    supplied_bytes = supplied.encode(errors="replace")
    compared = compare_digest(configured_bytes, supplied_bytes)
    return bool(settings.PHOTO_IMPORT_ENABLED and configured and valid_shape and compared)
