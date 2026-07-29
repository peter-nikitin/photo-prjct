"""Deployment-time guards for immutable processing-worker contracts."""

from django.conf import settings
from django.core.checks import Error, register

from processing.services.enrollment import CAPTURE_METADATA_CONFIGURATION


@register()
def capture_metadata_terminal_request_limit_check(**_: object) -> list[Error]:
    """Ensure the API can accept every terminal payload permitted by v1's snapshot."""
    worker = CAPTURE_METADATA_CONFIGURATION["worker"]
    assert isinstance(worker, dict)
    terminal_maximum = worker["terminal_result_max_bytes"]
    assert isinstance(terminal_maximum, int)
    configured_maximum = settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES
    if isinstance(configured_maximum, int) and configured_maximum >= terminal_maximum:
        return []
    return [
        Error(
            "PHOTO_PROCESSING_MAX_REQUEST_BYTES must be at least the immutable "
            "capture-metadata terminal_result_max_bytes.",
            id="processing.E001",
        )
    ]
