from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from picflow.event_management_access import can_inspect_event_photos, can_upload_event_photos


def photographer_navigation(request: HttpRequest) -> dict[str, Any]:
    user = request.user
    return {
        "photographer_upload_navigation": (
            can_inspect_event_photos(user) or can_upload_event_photos(user)
        )
    }
