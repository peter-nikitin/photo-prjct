from __future__ import annotations

import base64
import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from botocore.auth import S3SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import URLValidator
from ingestion.storage import (
    PrivateUploadStorage,
    StorageUnavailable,
    archive_object_key,
    s3_object_key,
    validate_accepted_preview_key,
    validate_attachment_filename,
    validate_original_key,
)

from picflow.event_media_storage import is_event_cover_key
from picflow.gallery_preview_grants import GALLERY_PREVIEW_URL_TTL_SECONDS

ARCHIVE_DOWNLOAD_TTL_SECONDS = 300


@dataclass(frozen=True)
class GalleryImageDeliverySettings:
    cdn_origin: str
    cdn_token_secret: str = field(repr=False)
    bucket: str
    ttl_seconds: int = field(default=GALLERY_PREVIEW_URL_TTL_SECONDS, init=False)

    def __post_init__(self) -> None:
        try:
            URLValidator(schemes=["https"])(self.cdn_origin)
            origin = urlsplit(self.cdn_origin)
            if (
                origin.username is not None
                or origin.password is not None
                or origin.path not in ("", "/")
                or "?" in self.cdn_origin
                or "#" in self.cdn_origin
                or any(character.isspace() for character in self.cdn_origin)
            ):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise ImproperlyConfigured("GALLERY_CDN_ORIGIN must be an HTTPS origin") from None
        if not isinstance(self.cdn_token_secret, str) or not 6 <= len(self.cdn_token_secret) <= 32:
            raise ImproperlyConfigured("GALLERY_CDN_TOKEN_SECRET must contain 6 to 32 characters")
        if (
            not isinstance(self.bucket, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket) is None
            or ".." in self.bucket
        ):
            raise ImproperlyConfigured("EVENT_MEDIA_S3_BUCKET must be a valid bucket name")

    @classmethod
    def from_django_settings(
        cls,
        *,
        bucket_setting: Literal[
            "EVENT_MEDIA_S3_BUCKET", "OPERATIONAL_S3_BUCKET"
        ] = "EVENT_MEDIA_S3_BUCKET",
    ) -> GalleryImageDeliverySettings:
        return cls(
            cdn_origin=getattr(settings, "GALLERY_CDN_ORIGIN", ""),
            cdn_token_secret=getattr(settings, "GALLERY_CDN_TOKEN_SECRET", ""),
            bucket=getattr(settings, bucket_setting),
        )


class GalleryImageUrlSigner:
    def __init__(
        self, config: GalleryImageDeliverySettings, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._config = config
        self._clock = clock

    def sign_accepted_preview(self, *, key: str, expires_in: int) -> str:
        """Sign an exact page-authorized preview without any storage or network calls."""
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or not 1 <= expires_in <= self._config.ttl_seconds
        ):
            raise ValueError("expires_in is outside the accepted preview limit")
        return self._preview_url(key=key, expires_in=expires_in)

    def preview_redirect_url(self, *, key: str) -> str:
        return self._preview_url(key=key, expires_in=settings.PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS)

    def _preview_url(self, *, key: str, expires_in: int) -> str:
        validate_accepted_preview_key(key)
        path = f"/{self._config.bucket}/{s3_object_key(key)}"
        return self._signed_storage_url(path=path, expires_in=expires_in)

    def archive_url(self, *, object_id: UUID, expires_in: int) -> str:
        if type(expires_in) is not int or not 1 <= expires_in <= ARCHIVE_DOWNLOAD_TTL_SECONDS:
            raise ValueError("expires_in is outside the archive limit")
        return self._signed_storage_url(
            path=f"/{self._config.bucket}/{archive_object_key(object_id)}",
            expires_in=expires_in,
        )

    def _signed_storage_url(self, *, path: str, expires_in: int) -> str:
        query = self._token_query(path, expires_in)
        endpoint = settings.PRIVATE_MEDIA_S3_ENDPOINT_URL.rstrip("/")
        try:
            origin = urlsplit(endpoint)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.username is not None
                or origin.password is not None
                or origin.path
                or "?" in endpoint
                or "#" in endpoint
                or any(character.isspace() for character in endpoint)
            ):
                raise ValueError
        except ValueError:
            raise StorageUnavailable() from None
        request = AWSRequest(method="GET", url=f"{endpoint}{path}?{query}")
        if (
            not settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID
            or not settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY
        ):
            raise StorageUnavailable()
        credentials = Credentials(
            settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID, settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY
        )
        S3SigV4QueryAuth(
            credentials, "s3", settings.PRIVATE_MEDIA_S3_REGION, expires=expires_in
        ).add_auth(request)
        return f"{self._config.cdn_origin.rstrip('/')}{path}?{urlsplit(str(request.url)).query}"

    def sign_event_cover(self, *, key: str) -> str:
        if not is_event_cover_key(key):
            raise ValueError("Invalid managed event cover object key")
        return f"{self._config.cdn_origin.rstrip('/')}/{self._config.bucket}/{key}"

    def original_url(self, *, key: str, attachment_filename: str | None) -> str:
        validate_original_key(key)
        if attachment_filename is not None:
            validate_attachment_filename(attachment_filename)
        path = f"/delivery/{key}/{attachment_filename or 'inline'}"
        query = self._token_query(path, settings.PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS)
        return f"{self._config.cdn_origin.rstrip('/')}{path}?{query}"

    def _token_query(self, path: str, expires_in: int) -> str:
        expires = int(self._clock()) + expires_in
        token = _cdn_token(path, expires=expires, secret=self._config.cdn_token_secret)
        return urlencode({"md5": token, "expires": expires})


def sign_private_archive(*, object_id: UUID, expires_in: int) -> str:
    """Issue short-lived CDN access to an exact private archive."""
    try:
        config = GalleryImageDeliverySettings.from_django_settings(
            bucket_setting="OPERATIONAL_S3_BUCKET"
        )
    except ImproperlyConfigured:
        raise StorageUnavailable() from None
    return GalleryImageUrlSigner(config).archive_url(object_id=object_id, expires_in=expires_in)


def _urlsafe_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _cdn_token(path: str, *, expires: int, secret: str) -> str:
    # Yandex CDN's secure-token protocol requires MD5, with no client-IP binding.
    return _urlsafe_base64(hashlib.md5(f"{expires}{path} {secret}".encode()).digest())


class CdnFinalObjectStorage(PrivateUploadStorage):
    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:
        try:
            signer = GalleryImageUrlSigner(GalleryImageDeliverySettings.from_django_settings())
        except ImproperlyConfigured:
            raise StorageUnavailable() from None
        if key.startswith("derivatives/") and attachment_filename is None:
            self._verify_final_image(key)
            return signer.preview_redirect_url(key=key)
        url = signer.original_url(key=key, attachment_filename=attachment_filename)
        self._verify_final_image(key)
        return url
