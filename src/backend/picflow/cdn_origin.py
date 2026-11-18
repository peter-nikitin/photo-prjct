import hmac
import re

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_safe
from ingestion.storage import ObjectMissing, PrivateUploadStorage, RangeNotSatisfiable, StorageError


@require_safe
def original_origin(
    request: HttpRequest, object_id: str, disposition: str
) -> HttpResponse | StreamingHttpResponse:
    secret = settings.IMAGE_ORIGIN_HEADER_SECRET
    supplied = request.headers.get("X-FindMe-Origin-Auth", "")
    if not secret or not hmac.compare_digest(secret.encode(), supplied.encode()):
        return HttpResponse(status=403)
    byte_range = request.headers.get("Range")
    if byte_range is not None and not re.fullmatch(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)", byte_range):
        return HttpResponse(status=416)
    try:
        opened = PrivateUploadStorage().open_final(
            key=f"originals/{object_id}", byte_range=byte_range
        )
    except RangeNotSatisfiable:
        return HttpResponse(status=416)
    except ObjectMissing:
        return HttpResponse(status=404)
    except (StorageError, ValueError):
        return HttpResponse(status=503)

    response = FileResponse(
        opened.body, content_type=opened.content_type, status=206 if opened.content_range else 200
    )
    response["Content-Length"] = str(opened.size)
    response["X-Accel-Buffering"] = "no"
    response["Accept-Ranges"] = "bytes"
    if opened.content_range:
        response["Content-Range"] = opened.content_range
    response["Content-Disposition"] = (
        "inline" if disposition == "inline" else f'attachment; filename="{disposition}"'
    )
    response["Cache-Control"] = "private, no-store"
    return response
