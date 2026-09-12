"""Ingestion-owned context for one fixed-event workspace."""

from uuid import UUID

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from feature_flags.registry import YANDEX_DISK_IMPORT
from feature_flags.services import is_enabled

_URL_BATCH = UUID("00000000-0000-4000-8000-000000000001")
_URL_ITEM = UUID("00000000-0000-4000-8000-000000000002")


def upload_workspace_context(request: HttpRequest) -> dict[str, object]:
    return {
        "upload_limits": {
            "max_files": settings.PHOTO_UPLOAD_MAX_FILES,
            "max_files_label": f"{settings.PHOTO_UPLOAD_MAX_FILES:,}".replace(",", " "),
            "max_file_bytes": settings.PHOTO_UPLOAD_MAX_FILE_BYTES,
            "max_file_megabytes": settings.PHOTO_UPLOAD_MAX_FILE_BYTES // (1024 * 1024),
            "registration_chunk": settings.PHOTO_UPLOAD_REGISTRATION_CHUNK,
            "concurrency": settings.PHOTO_UPLOAD_CONCURRENCY,
        },
        "upload_state": "empty",
        "upload_control_urls": _upload_control_urls(),
        "photo_import_enabled": settings.PHOTO_IMPORT_ENABLED
        and is_enabled(YANDEX_DISK_IMPORT, request.user),
        "photo_import_history_enabled": True,
        "photo_import_urls": _photo_import_urls(),
    }


def _upload_control_urls() -> dict[str, str]:
    batch = str(_URL_BATCH)
    item = str(_URL_ITEM)

    def item_url(name: str) -> str:
        return (
            reverse(name, args=[_URL_BATCH, _URL_ITEM])
            .replace(batch, "{batch}")
            .replace(item, "{item}")
        )

    return {
        "register": reverse("upload_items_register", args=[_URL_BATCH]).replace(batch, "{batch}"),
        "authorize": item_url("upload_item_authorize"),
        "retry": item_url("upload_item_retry"),
        "confirm": item_url("upload_item_confirm"),
        "failed": item_url("upload_item_failed"),
        "finalize": reverse("upload_batch_finalize", args=[_URL_BATCH]).replace(batch, "{batch}"),
        "resume_manifest": reverse("upload_batch_resume_manifest", args=[_URL_BATCH]).replace(
            batch, "{batch}"
        ),
    }


def _photo_import_urls() -> dict[str, str]:
    batch = str(_URL_BATCH)
    return {
        "collection": reverse("import_collection"),
        "detail": reverse("import_detail", args=[_URL_BATCH]).replace(batch, "{batch}"),
        "items": reverse("import_items", args=[_URL_BATCH]).replace(batch, "{batch}"),
        "retry": reverse("import_retry", args=[_URL_BATCH]).replace(batch, "{batch}"),
    }
