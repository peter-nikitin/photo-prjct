from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def photographer_navigation(request: HttpRequest) -> dict[str, Any]:
    user = request.user
    return {
        "photographer_upload_navigation": (
            settings.PHOTO_UPLOAD_ENABLED
            and user.is_authenticated
            and user.has_perm("ingestion.upload_photos")
        )
    }
