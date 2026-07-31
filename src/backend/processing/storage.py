"""Short-lived exact-object grants and verification for private processing media."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.utils import timezone
from ingestion.storage import (
    ObjectChanged,
    ObjectMismatch,
    ObjectMissing,
    StorageError,
    StorageUnavailable,
)

_FINAL_KEY = re.compile(r"originals/[0-9a-f]{32}")
_PREVIEW_STAGING_KEY = re.compile(
    r"processing-staging/previews/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/preview-small-v1\.jpg"
)
_PREVIEW_FINAL_KEY = re.compile(
    r"derivatives/previews/[A-Za-z0-9_-]{1,32}/preview-small-v1/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-"
    r"[0-9a-f]{64}\.jpg"
)
_ETAG = re.compile(r'"([^"\r\n]+)"')


class _S3Client(Protocol):
    def generate_presigned_url(self, **kwargs: Any) -> str: ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def copy_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DownloadGrant:
    """An in-memory API response value.  It must never be stored in a model or log."""

    url: str
    expires_at: datetime


@dataclass(frozen=True)
class PreviewUploadGrant:
    """A non-persistable PUT grant for one current attempt staging object."""

    url: str
    expires_at: datetime


@dataclass(frozen=True)
class PreviewObject:
    """Object facts Django independently established while streaming private bytes."""

    etag_wire: str
    etag_value: str
    byte_size: int
    content_type: str
    sha256: str
    width: int
    height: int


class ObjectConflict(StorageError):
    """A final derivative key was already present when Django inspected it."""

    code = "object_conflict"
    message = "A stored object conflicts with the requested publication."


class ExactObjectDownloadStorage:
    """Create a GET-only grant for a final key selected by Django, never by a worker request."""

    def __init__(self, client: _S3Client | None = None) -> None:
        self._bucket = settings.PRIVATE_MEDIA_S3_BUCKET
        self._ttl_seconds = settings.PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                aws_access_key_id=settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
                endpoint_url=settings.PRIVATE_MEDIA_S3_ENDPOINT_URL,
                region_name=settings.PRIVATE_MEDIA_S3_REGION,
                config=Config(signature_version="s3v4"),
            )
        )

    def create_download_grant(
        self, *, final_key: str, max_ttl_seconds: int | None = None
    ) -> DownloadGrant:
        _validate_final_key(final_key)
        if max_ttl_seconds is None:
            max_ttl_seconds = self._ttl_seconds
        if isinstance(max_ttl_seconds, bool) or not isinstance(max_ttl_seconds, int):
            raise ValueError("max_ttl_seconds must be an integer")
        expires_in = min(self._ttl_seconds, max_ttl_seconds)
        if expires_in < 1:
            raise ValueError("download grant must have positive remaining lease time")
        try:
            url = self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": final_key,
                    "ResponseContentType": "image/jpeg",
                    "ResponseContentDisposition": 'attachment; filename="photo.jpg"',
                },
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
            if not isinstance(url, str) or not url:
                raise TypeError
        except ClientError as error:
            if _status(error) == 404:
                raise ObjectMissing() from None
            raise StorageUnavailable() from None
        except (BotoCoreError, TypeError):
            raise StorageUnavailable() from None
        return DownloadGrant(url=url, expires_at=timezone.now() + timedelta(seconds=expires_in))


class ExactPreviewStorage:
    """Django-only exact staging access and content-addressed preview publication primitives."""

    def __init__(self, client: _S3Client | None = None) -> None:
        self._bucket = settings.PRIVATE_MEDIA_S3_BUCKET
        self._ttl_seconds = settings.PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                aws_access_key_id=settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
                endpoint_url=settings.PRIVATE_MEDIA_S3_ENDPOINT_URL,
                region_name=settings.PRIVATE_MEDIA_S3_REGION,
                config=Config(signature_version="s3v4"),
            )
        )

    def create_download_grant(
        self, *, final_key: str, max_ttl_seconds: int | None = None
    ) -> DownloadGrant:
        """Create a GET-only grant for one accepted, content-addressed preview."""
        _validate_preview_final_key(final_key)
        expires_in = self._expires_in(max_ttl_seconds)
        try:
            url = self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": final_key,
                    "ResponseContentType": "image/jpeg",
                    "ResponseContentDisposition": 'attachment; filename="preview.jpg"',
                },
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
            if not isinstance(url, str) or not url:
                raise TypeError
        except ClientError as error:
            if _status(error) == 404:
                raise ObjectMissing() from None
            raise StorageUnavailable() from None
        except (BotoCoreError, TypeError):
            raise StorageUnavailable() from None
        return DownloadGrant(url=url, expires_at=timezone.now() + timedelta(seconds=expires_in))

    def create_upload_grant(
        self, *, staging_key: str, max_ttl_seconds: int | None = None
    ) -> PreviewUploadGrant:
        _validate_preview_staging_key(staging_key)
        expires_in = self._expires_in(max_ttl_seconds)
        try:
            url = self._client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": staging_key,
                    "ContentType": "image/jpeg",
                },
                ExpiresIn=expires_in,
                HttpMethod="PUT",
            )
            if not isinstance(url, str) or not url:
                raise TypeError
        except (BotoCoreError, ClientError, TypeError):
            raise StorageUnavailable() from None
        return PreviewUploadGrant(
            url=url, expires_at=timezone.now() + timedelta(seconds=expires_in)
        )

    def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
        """HEAD then stream-hash one permitted preview key without retaining its bytes."""
        _validate_preview_key(key)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        identity = self._inspect(key)
        if identity.byte_size > max_bytes or identity.content_type != "image/jpeg":
            raise ObjectMismatch()
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=key,
                IfMatch=identity.etag_wire,
            )
            body = response["Body"]
            response_size = response["ContentLength"]
            response_type = response["ContentType"]
            if (
                isinstance(response_size, bool)
                or not isinstance(response_size, int)
                or response_size != identity.byte_size
                or not isinstance(response_type, str)
                or response_type.strip().lower() != identity.content_type
                or not callable(getattr(body, "read", None))
                or not callable(getattr(body, "close", None))
            ):
                raise TypeError
        except ClientError as error:
            raise _preview_mapped_error(error) from None
        except BotoCoreError:
            raise StorageUnavailable() from None
        except (KeyError, AttributeError, TypeError):
            raise ObjectMismatch() from None

        import hashlib

        digest = hashlib.sha256()
        dimensions = _JpegDimensions()
        received = 0
        try:
            while True:
                chunk = body.read(64 * 1024)
                if not isinstance(chunk, bytes):
                    raise TypeError
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise ObjectMismatch()
                digest.update(chunk)
                dimensions.feed(chunk)
        except ObjectMismatch:
            raise
        except BotoCoreError:
            raise StorageUnavailable() from None
        except (AttributeError, TypeError, ValueError):
            raise ObjectMismatch() from None
        finally:
            try:
                body.close()
            except BotoCoreError:
                raise StorageUnavailable() from None
            except (AttributeError, TypeError):
                raise ObjectMismatch() from None
        if received != identity.byte_size:
            raise ObjectChanged()
        width, height = dimensions.finish()
        return PreviewObject(
            etag_wire=identity.etag_wire,
            etag_value=identity.etag_value,
            byte_size=identity.byte_size,
            content_type=identity.content_type,
            sha256=digest.hexdigest(),
            width=width,
            height=height,
        )

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject:
        """Copy a source conditionally; callers fully verify the content-addressed final key."""
        _validate_preview_staging_key(staging_key)
        _validate_preview_final_key(final_key)
        expected_etag = _etag_value(source_etag)
        try:
            self._inspect(final_key)
        except ObjectMissing:
            pass
        else:
            raise ObjectConflict()
        source = self._inspect(staging_key, if_match=source_etag)
        if source.etag_value != expected_etag:
            raise ObjectChanged()
        try:
            response = self._client.copy_object(
                Bucket=self._bucket,
                Key=final_key,
                CopySource={"Bucket": self._bucket, "Key": staging_key},
                CopySourceIfMatch=source_etag,
            )
            copied_etag = _etag_value(response["CopyObjectResult"]["ETag"])
        except ClientError as error:
            raise _preview_mapped_error(error) from None
        except BotoCoreError:
            raise StorageUnavailable() from None
        except (KeyError, TypeError, ValueError):
            raise ObjectMismatch() from None
        if copied_etag != source.etag_value:
            raise ObjectMismatch()
        final = self._inspect(final_key)
        if (
            final.etag_value != source.etag_value
            or final.byte_size != source.byte_size
            or final.content_type != source.content_type
        ):
            raise ObjectMismatch()
        return final

    def _expires_in(self, max_ttl_seconds: int | None) -> int:
        if max_ttl_seconds is None:
            max_ttl_seconds = self._ttl_seconds
        if isinstance(max_ttl_seconds, bool) or not isinstance(max_ttl_seconds, int):
            raise ValueError("max_ttl_seconds must be an integer")
        expires_in = min(self._ttl_seconds, max_ttl_seconds)
        if expires_in < 1:
            raise ValueError("preview grant must have positive remaining lease time")
        return expires_in

    def _inspect(self, key: str, *, if_match: str | None = None) -> PreviewObject:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if if_match is not None:
            _etag_value(if_match)
            kwargs["IfMatch"] = if_match
        try:
            response = self._client.head_object(**kwargs)
        except ClientError as error:
            raise _preview_mapped_error(error) from None
        except BotoCoreError:
            raise StorageUnavailable() from None
        try:
            etag_wire = response["ETag"]
            byte_size = response["ContentLength"]
            content_type = response["ContentType"]
            etag_value = _etag_value(etag_wire)
            if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
                raise TypeError
            if not isinstance(content_type, str) or not content_type.strip():
                raise TypeError
        except (KeyError, TypeError, ValueError):
            raise ObjectMismatch() from None
        return PreviewObject(
            etag_wire=etag_wire,
            etag_value=etag_value,
            byte_size=byte_size,
            content_type=content_type.strip().lower(),
            sha256="",
            width=0,
            height=0,
        )


def _validate_final_key(key: object) -> None:
    if not isinstance(key, str) or _FINAL_KEY.fullmatch(key) is None:
        raise ValueError("invalid final object key")


def _validate_preview_staging_key(key: object) -> None:
    if not isinstance(key, str) or _PREVIEW_STAGING_KEY.fullmatch(key) is None:
        raise ValueError("invalid preview staging object key")


def _validate_preview_final_key(key: object) -> None:
    if not isinstance(key, str) or _PREVIEW_FINAL_KEY.fullmatch(key) is None:
        raise ValueError("invalid preview final object key")


def _validate_preview_key(key: object) -> None:
    if not isinstance(key, str) or (
        _PREVIEW_STAGING_KEY.fullmatch(key) is None and _PREVIEW_FINAL_KEY.fullmatch(key) is None
    ):
        raise ValueError("invalid preview object key")


def _etag_value(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid ETag")
    matched = _ETAG.fullmatch(value)
    if matched is None or not matched.group(1).strip():
        raise ValueError("invalid ETag")
    return matched.group(1)


def _status(error: ClientError) -> int | None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _preview_mapped_error(error: ClientError) -> StorageError:
    status = _status(error)
    if status == 404:
        return ObjectMissing()
    if status in {409, 412}:
        return ObjectChanged()
    return StorageUnavailable()


class _JpegDimensions:
    """Incrementally inspect the JPEG frame header while SHA-256 consumes the stream."""

    _SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._position = 0
        self._dimensions: tuple[int, int] | None = None

    def feed(self, data: bytes) -> None:
        if self._dimensions is not None:
            return
        self._buffer.extend(data)
        if len(self._buffer) >= 2 and self._buffer[:2] != b"\xff\xd8":
            raise ObjectMismatch()
        if len(self._buffer) < 4:
            return
        self._position = max(self._position, 2)
        while self._dimensions is None:
            if self._position + 2 > len(self._buffer):
                return
            if self._buffer[self._position] != 0xFF:
                raise ObjectMismatch()
            marker_start = self._position
            while self._position < len(self._buffer) and self._buffer[self._position] == 0xFF:
                self._position += 1
            if self._position >= len(self._buffer):
                self._position = marker_start
                return
            marker = self._buffer[self._position]
            self._position += 1
            if marker in {0xD8, 0xD9, *range(0xD0, 0xD8)}:
                continue
            if self._position + 2 > len(self._buffer):
                self._position = marker_start
                return
            length = (self._buffer[self._position] << 8) | self._buffer[self._position + 1]
            if length < 2:
                raise ObjectMismatch()
            segment_end = self._position + length
            if segment_end > len(self._buffer):
                self._position = marker_start
                return
            if marker in self._SOF:
                if length < 8:
                    raise ObjectMismatch()
                height = (self._buffer[self._position + 3] << 8) | self._buffer[self._position + 4]
                width = (self._buffer[self._position + 5] << 8) | self._buffer[self._position + 6]
                if width < 1 or height < 1:
                    raise ObjectMismatch()
                self._dimensions = (width, height)
                return
            if marker == 0xDA:
                raise ObjectMismatch()
            self._position = segment_end

    def finish(self) -> tuple[int, int]:
        if self._dimensions is None:
            raise ObjectMismatch()
        return self._dimensions
