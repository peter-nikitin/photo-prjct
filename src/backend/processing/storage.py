"""Short-lived, exact-object download grants for claimed processing jobs."""

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
from ingestion.storage import ObjectMissing, StorageUnavailable

_FINAL_KEY = re.compile(r"originals/[0-9a-f]{32}")


class _S3Client(Protocol):
    def generate_presigned_url(self, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class DownloadGrant:
    """An in-memory API response value.  It must never be stored in a model or log."""

    url: str
    expires_at: datetime


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


def _validate_final_key(key: object) -> None:
    if not isinstance(key, str) or _FINAL_KEY.fullmatch(key) is None:
        raise ValueError("invalid final object key")


def _status(error: ClientError) -> int | None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None
