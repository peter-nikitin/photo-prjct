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

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_INCOMING_KEY = re.compile(rf"incoming/{_UUID}/{_UUID}")
_FINAL_KEY = re.compile(r"originals/[0-9a-f]{32}")
_ETAG = re.compile(r'"([^"\r\n]+)"')


class _S3Client(Protocol):
    def generate_presigned_post(self, **kwargs: Any) -> dict[str, Any]: ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def copy_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class UploadGrant:
    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class ObjectIdentity:
    etag_wire: str
    etag_value: str
    size: int
    content_type: str


class StorageError(Exception):
    code = "storage_error"
    message = "Object storage operation failed."

    def __init__(self) -> None:
        super().__init__(self.message)


class StorageUnavailable(StorageError):
    code = "storage_unavailable"
    message = "Object storage is unavailable."


class ObjectMissing(StorageError):
    code = "object_missing"
    message = "Stored object was not found."


class ObjectChanged(StorageError):
    code = "object_changed"
    message = "Stored object changed during verification."


class ObjectMismatch(StorageError):
    code = "object_mismatch"
    message = "Stored object metadata did not match."


class PrivateUploadStorage:
    def __init__(self, client: _S3Client | None = None) -> None:
        self._bucket = settings.PRIVATE_MEDIA_S3_BUCKET
        self._max_file_bytes = settings.PHOTO_UPLOAD_MAX_FILE_BYTES
        self._grant_ttl_seconds = settings.PHOTO_UPLOAD_GRANT_TTL_SECONDS
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

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        _validate_incoming_key(incoming_key)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("max_bytes must be an integer")
        if not 1 <= max_bytes <= self._max_file_bytes:
            raise ValueError("max_bytes is outside the configured upload limit")

        expires_in = self._grant_ttl_seconds
        try:
            response = self._client.generate_presigned_post(
                Bucket=self._bucket,
                Key=incoming_key,
                Fields={"Content-Type": "image/jpeg"},
                Conditions=[
                    {"bucket": self._bucket},
                    {"key": incoming_key},
                    {"Content-Type": "image/jpeg"},
                    ["content-length-range", 1, max_bytes],
                ],
                ExpiresIn=expires_in,
            )
            url = response["url"]
            fields = response["fields"]
            if not isinstance(url, str) or not isinstance(fields, dict):
                raise TypeError
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in fields.items()
            ):
                raise TypeError
        except (BotoCoreError, ClientError, KeyError, TypeError):
            raise StorageUnavailable() from None

        return UploadGrant(
            url=url, fields=fields, expires_at=timezone.now() + timedelta(seconds=expires_in)
        )

    def inspect(self, *, key: str) -> ObjectIdentity:
        _validate_managed_key(key)
        return self._inspect(key)

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes:
        _validate_managed_key(key)
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end < start
        ):
            raise ValueError("invalid byte range")
        _etag_value(etag_wire)

        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=key,
                Range=f"bytes={start}-{end}",
                IfMatch=etag_wire,
            )
            body = response["Body"]
            try:
                result = body.read()
            finally:
                body.close()
            if not isinstance(result, bytes):
                raise TypeError
            return result
        except ClientError as error:
            raise _mapped_error(error) from None
        except (BotoCoreError, KeyError, AttributeError, TypeError):
            raise StorageUnavailable() from None

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity:
        _validate_incoming_key(incoming_key)
        _validate_final_key(final_key)
        expected_etag = _etag_value(etag_wire)

        source = self._inspect(incoming_key, if_match=etag_wire)
        if source.etag_value != expected_etag:
            raise ObjectChanged()

        try:
            response = self._client.copy_object(
                Bucket=self._bucket,
                Key=final_key,
                CopySource={"Bucket": self._bucket, "Key": incoming_key},
                CopySourceIfMatch=etag_wire,
            )
        except ClientError as error:
            raise _mapped_error(error) from None
        except BotoCoreError:
            raise StorageUnavailable() from None
        try:
            copy_etag_value = _etag_value(response["CopyObjectResult"]["ETag"])
        except (KeyError, TypeError, ValueError):
            raise ObjectMismatch() from None

        if copy_etag_value != source.etag_value:
            raise ObjectMismatch()

        final = self._inspect(final_key)
        if (
            final.etag_value != source.etag_value
            or final.size != source.size
            or final.content_type != source.content_type
        ):
            raise ObjectMismatch()
        return final

    def delete(self, *, key: str) -> None:
        _validate_managed_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _status(error) == 404:
                return
            raise StorageUnavailable() from None
        except BotoCoreError:
            raise StorageUnavailable() from None

    def _inspect(self, key: str, if_match: str | None = None) -> ObjectIdentity:
        kwargs = {"Bucket": self._bucket, "Key": key}
        if if_match is not None:
            _etag_value(if_match)
            kwargs["IfMatch"] = if_match
        try:
            response = self._client.head_object(**kwargs)
        except ClientError as error:
            raise _mapped_error(error) from None
        except BotoCoreError:
            raise StorageUnavailable() from None

        try:
            etag_wire = response["ETag"]
            size = response["ContentLength"]
            content_type = response["ContentType"]
            etag_value = _etag_value(etag_wire)
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise TypeError
            if not isinstance(content_type, str) or not content_type.strip():
                raise TypeError
        except (KeyError, TypeError, ValueError):
            raise ObjectMismatch() from None

        return ObjectIdentity(etag_wire, etag_value, size, content_type.strip().lower())


def _validate_incoming_key(key: str) -> None:
    if not isinstance(key, str) or _INCOMING_KEY.fullmatch(key) is None:
        raise ValueError("invalid incoming object key")


def _validate_final_key(key: str) -> None:
    if not isinstance(key, str) or _FINAL_KEY.fullmatch(key) is None:
        raise ValueError("invalid final object key")


def _validate_managed_key(key: str) -> None:
    if not isinstance(key, str) or (
        _INCOMING_KEY.fullmatch(key) is None and _FINAL_KEY.fullmatch(key) is None
    ):
        raise ValueError("invalid managed object key")


def _etag_value(etag_wire: object) -> str:
    if not isinstance(etag_wire, str):
        raise TypeError("invalid ETag")
    matched = _ETAG.fullmatch(etag_wire)
    if matched is None or not matched.group(1).strip():
        raise ValueError("invalid ETag")
    return matched.group(1)


def _status(error: ClientError) -> int | None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _mapped_error(error: ClientError) -> StorageError:
    status = _status(error)
    if status == 404:
        return ObjectMissing()
    if status in {409, 412}:
        return ObjectChanged()
    return StorageUnavailable()
