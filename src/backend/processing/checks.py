"""Deployment-time guards for immutable processing-worker contracts."""

from django.conf import settings
from django.core.checks import Error, register

from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    GENERATE_PREVIEW_CONFIGURATION,
    capture_metadata_configuration,
)


@register()
def capture_metadata_terminal_request_limit_check(**_: object) -> list[Error]:
    """Ensure the API can accept every terminal payload permitted by active snapshots."""
    terminal_maximum = max(
        _terminal_result_maximum(configuration)
        for configuration in (
            capture_metadata_configuration("Etc/UTC"),
            FACE_EMBEDDING_CONFIGURATION,
            GENERATE_PREVIEW_CONFIGURATION,
        )
    )
    configured_maximum = settings.PHOTO_PROCESSING_MAX_REQUEST_BYTES
    if isinstance(configured_maximum, int) and configured_maximum >= terminal_maximum:
        return []
    return [
        Error(
            "PHOTO_PROCESSING_MAX_REQUEST_BYTES must be at least the immutable "
            "processor terminal_result_max_bytes.",
            id="processing.E001",
        )
    ]


def _terminal_result_maximum(configuration: dict[str, object]) -> int:
    worker = configuration["worker"]
    assert isinstance(worker, dict)
    terminal_maximum = worker["terminal_result_max_bytes"]
    assert isinstance(terminal_maximum, int)
    return terminal_maximum
