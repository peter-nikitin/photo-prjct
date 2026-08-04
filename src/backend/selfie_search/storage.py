"""Private, application-owned temporary storage for submitted selfie bytes."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.utils import timezone
from ingestion.storage import ObjectMissing, StorageUnavailable

_KEY = re.compile(r"selfie-search/[0-9a-f]{32}")
_CONTENT_TYPES = {"image/jpeg", "image/png"}
_FEEDBACK_KEY = re.compile(r"[0-9a-f]{32}")
_FEEDBACK_CONTENT_TYPES = {"image/jpeg", "image/png"}


class _S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def generate_presigned_url(self, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class StoredTemporarySelfie:
    key: str
    size: int
    content_type: str


@dataclass(frozen=True)
class DownloadGrant:
    url: str
    expires_at: datetime


class TemporarySelfieStorage:
    def __init__(self, client: _S3Client | None = None) -> None:
        self._bucket = settings.PRIVATE_MEDIA_S3_BUCKET
        self._max_upload_bytes = settings.SELFIE_SEARCH_MAX_UPLOAD_BYTES
        self._ttl_seconds = settings.SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS
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

    def put(self, *, key: str, content: bytes, content_type: str) -> StoredTemporarySelfie:
        _validate_key(key)
        _validate_content(content, content_type, maximum=self._max_upload_bytes)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                ContentLength=len(content),
                ACL="private",
            )
        except (BotoCoreError, ClientError, TypeError):
            raise StorageUnavailable() from None
        return StoredTemporarySelfie(key=key, size=len(content), content_type=content_type)

    def delete(self, *, key: str) -> None:
        _validate_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _status(error) != 404:
                raise StorageUnavailable() from None
        except (BotoCoreError, TypeError):
            raise StorageUnavailable() from None

    def inspect(self, *, key: str) -> StoredTemporarySelfie:
        _validate_key(key)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _status(error) == 404:
                raise ObjectMissing() from None
            raise StorageUnavailable() from None
        except (BotoCoreError, TypeError):
            raise StorageUnavailable() from None
        content_type = response.get("ContentType")
        size = response.get("ContentLength")
        if (
            content_type not in _CONTENT_TYPES
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 1 <= size <= self._max_upload_bytes
        ):
            raise StorageUnavailable()
        return StoredTemporarySelfie(key=key, size=size, content_type=content_type)

    def create_download_grant(
        self, *, key: str, max_ttl_seconds: int | None = None
    ) -> DownloadGrant:
        _validate_key(key)
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
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
            if not isinstance(url, str) or not url:
                raise TypeError
        except (BotoCoreError, ClientError, TypeError):
            raise StorageUnavailable() from None
        return DownloadGrant(url=url, expires_at=timezone.now() + timedelta(seconds=expires_in))


class FeedbackSelfieStorage:
    def __init__(self, client: _S3Client | None = None) -> None:
        self._bucket = settings.SELFIE_FEEDBACK_S3_BUCKET
        self._max_upload_bytes = settings.SELFIE_FEEDBACK_MAX_UPLOAD_BYTES
        self._ttl_seconds = settings.SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS
        self._kms_key_id = settings.SELFIE_FEEDBACK_KMS_KEY_ID
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                aws_access_key_id=settings.SELFIE_FEEDBACK_S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY,
                endpoint_url=settings.SELFIE_FEEDBACK_S3_ENDPOINT_URL,
                region_name=settings.SELFIE_FEEDBACK_S3_REGION,
                config=Config(signature_version="s3v4"),
            )
        )

    def put(self, *, content: bytes, content_type: str) -> StoredTemporarySelfie:
        _validate_feedback_content(content, content_type, maximum=self._max_upload_bytes)
        key = secrets.token_hex(16)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                ContentLength=len(content),
                ACL="private",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=self._kms_key_id,
            )
        except (BotoCoreError, ClientError, TypeError):
            raise StorageUnavailable() from None
        return StoredTemporarySelfie(key=key, size=len(content), content_type=content_type)

    def delete(self, *, key: str) -> None:
        _validate_feedback_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _status(error) != 404:
                raise StorageUnavailable() from None
        except (BotoCoreError, TypeError):
            raise StorageUnavailable() from None

    def inspect(self, *, key: str) -> StoredTemporarySelfie:
        _validate_feedback_key(key)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _status(error) == 404:
                raise ObjectMissing() from None
            raise StorageUnavailable() from None
        except (BotoCoreError, TypeError):
            raise StorageUnavailable() from None
        content_type = response.get("ContentType")
        size = response.get("ContentLength")
        if (
            content_type not in _FEEDBACK_CONTENT_TYPES
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 1 <= size <= self._max_upload_bytes
        ):
            raise StorageUnavailable()
        return StoredTemporarySelfie(key=key, size=size, content_type=content_type)

    def create_download_grant(self, *, key: str) -> DownloadGrant:
        _validate_feedback_key(key)
        try:
            url = self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._ttl_seconds,
                HttpMethod="GET",
            )
            if not isinstance(url, str) or not url:
                raise TypeError
        except (BotoCoreError, ClientError, TypeError):
            raise StorageUnavailable() from None
        return DownloadGrant(
            url=url,
            expires_at=timezone.now() + timedelta(seconds=self._ttl_seconds),
        )


def _validate_key(key: object) -> None:
    if not isinstance(key, str) or _KEY.fullmatch(key) is None:
        raise ValueError("invalid temporary selfie key")


def _validate_content(content: object, content_type: object, *, maximum: int) -> None:
    if not isinstance(content, bytes) or not content or len(content) > maximum:
        raise ValueError("invalid temporary selfie content")
    if content_type not in _CONTENT_TYPES:
        raise ValueError("invalid temporary selfie content type")


def _validate_feedback_key(key: object) -> None:
    if not isinstance(key, str) or _FEEDBACK_KEY.fullmatch(key) is None:
        raise ValueError("invalid feedback selfie key")


def _validate_feedback_content(content: object, content_type: object, *, maximum: int) -> None:
    if not isinstance(content, bytes) or not content or len(content) > maximum:
        raise ValueError("invalid feedback selfie content")
    if content_type not in _FEEDBACK_CONTENT_TYPES:
        raise ValueError("invalid feedback selfie content type")


def _status(error: ClientError) -> int | None:
    value = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return value if isinstance(value, int) else None
