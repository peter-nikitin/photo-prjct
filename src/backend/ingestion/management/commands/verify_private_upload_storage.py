from __future__ import annotations

import base64
import secrets
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingestion.storage import PrivateUploadStorage, StorageError, UploadGrant

# Valid one-pixel JPEG. Keeping the fixture in-process prevents a deployment probe from
# depending on a mutable file in the container image.
_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oA"
    "DAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAA"
    "AP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAA"
    "AP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAAB//xAAUEQEA"
    "AAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EH//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/EH//xAAUEAEA"
    "AAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EH//2Q=="
)


class Command(BaseCommand):
    help = "Exercise the private Object Storage upload contract against real storage."

    def add_arguments(self, parser) -> None:  # noqa: ANN001
        parser.add_argument("--confirm-real-storage", action="store_true")
        parser.add_argument("--origin", required=True)

    def handle(self, *args, **options) -> None:  # noqa: ANN002, ANN003
        if not settings.PHOTO_UPLOAD_ENABLED:
            raise CommandError("PHOTO_UPLOAD_ENABLED must be True for the storage probe")
        if not options["confirm_real_storage"]:
            raise CommandError("Pass --confirm-real-storage to permit temporary object writes")

        origin = options["origin"]
        if origin not in settings.PRIVATE_MEDIA_ALLOWED_ORIGINS:
            raise CommandError("--origin must exactly match a configured private-media origin")

        storage = PrivateUploadStorage()
        batch_id = uuid4()
        item_id = uuid4()
        incoming_key = f"incoming/{batch_id}/{item_id}"
        final_key = f"originals/{secrets.token_hex(16)}"
        contract_max = max(len(_JPEG), min(settings.PHOTO_UPLOAD_MAX_FILE_BYTES, 1024))

        try:
            grant = storage.create_presigned_post(incoming_key=incoming_key, max_bytes=contract_max)
            self._expect_rejected(
                grant,
                _JPEG,
                fields={**grant.fields, "key": f"incoming/{batch_id}/{uuid4()}"},
            )
            self._expect_rejected(
                grant,
                _JPEG,
                fields={**grant.fields, "Content-Type": "image/png"},
            )
            # Yandex Object Storage tolerates small multipart-boundary overages around the
            # policy edge. A 2x payload proves that the real service enforces a meaningful
            # upper bound; confirmation still requires the exact registered object size.
            self._expect_rejected(grant, b"x" * (contract_max * 2))
            _post_form(grant.url, grant.fields, _JPEG)

            source = storage.inspect(key=incoming_key)
            if source.size != len(_JPEG) or source.content_type != "image/jpeg":
                raise CommandError("Uploaded object metadata does not match the contract fixture")
            if (
                storage.read_range(key=incoming_key, etag_wire=source.etag_wire, start=0, end=1)
                != _JPEG[:2]
            ):
                raise CommandError("Conditional first-byte read returned unexpected data")
            tail_start = len(_JPEG) - 2
            if (
                storage.read_range(
                    key=incoming_key,
                    etag_wire=source.etag_wire,
                    start=tail_start,
                    end=len(_JPEG) - 1,
                )
                != _JPEG[-2:]
            ):
                raise CommandError("Conditional last-byte read returned unexpected data")

            promoted = storage.promote(
                incoming_key=incoming_key,
                final_key=final_key,
                etag_wire=source.etag_wire,
            )
            if promoted.size != source.size or promoted.etag_value != source.etag_value:
                raise CommandError("Promoted object identity does not match its source")

            anonymous_status, _ = _request("GET", _object_url(final_key))
            if anonymous_status not in {401, 403, 404}:
                raise CommandError("Private object is anonymously readable")
            self._verify_cors(grant.url, origin)
        except (StorageError, urllib.error.URLError) as error:
            raise CommandError("Private storage contract verification failed") from error
        finally:
            for key in (incoming_key, final_key):
                try:
                    storage.delete(key=key)
                except StorageError:
                    self.stderr.write("Warning: temporary contract object cleanup failed.")

        self.stdout.write(self.style.SUCCESS("Private upload storage contract verified."))

    def _expect_rejected(
        self,
        grant: UploadGrant,
        payload: bytes,
        *,
        fields: dict[str, str] | None = None,
    ) -> None:
        try:
            _post_form(grant.url, fields or grant.fields, payload)
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                return
            raise
        raise CommandError("Object Storage accepted a request forbidden by the upload policy")

    def _verify_cors(self, url: str, origin: str) -> None:
        status, headers = _request(
            "OPTIONS",
            url,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        if status < 200 or status >= 300:
            raise CommandError("Object Storage CORS preflight failed")
        if headers.get("access-control-allow-origin") != origin:
            raise CommandError("Object Storage returned an unexpected allowed origin")
        methods = {
            value.strip().upper()
            for value in headers.get("access-control-allow-methods", "").split(",")
        }
        allowed_headers = {
            value.strip().lower()
            for value in headers.get("access-control-allow-headers", "").split(",")
        }
        if "POST" not in methods or "content-type" not in allowed_headers:
            raise CommandError("Object Storage CORS policy does not allow the upload request")


def _post_form(url: str, fields: dict[str, str], payload: bytes) -> None:
    boundary = f"----photo-prjct-{secrets.token_hex(16)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="file"; filename="contract.jpg"\r\n')
    body.extend(b"Content-Type: image/jpeg\r\n\r\n")
    body.extend(payload)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status not in {200, 201, 204}:
            raise CommandError("Object Storage rejected the valid upload contract request")


def _request(
    method: str, url: str, *, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, {
                name.lower(): value for name, value in response.headers.items()
            }
    except urllib.error.HTTPError as error:
        return error.code, {name.lower(): value for name, value in error.headers.items()}


def _object_url(key: str) -> str:
    endpoint = settings.PRIVATE_MEDIA_S3_ENDPOINT_URL.rstrip("/")
    bucket = urllib.parse.quote(settings.PRIVATE_MEDIA_S3_BUCKET, safe="")
    object_key = urllib.parse.quote(key, safe="/")
    return f"{endpoint}/{bucket}/{object_key}"
