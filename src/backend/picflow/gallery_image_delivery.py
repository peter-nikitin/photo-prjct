from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import URLValidator
from ingestion.storage import validate_accepted_preview_key

from picflow.gallery_preview_grants import GALLERY_PREVIEW_URL_TTL_SECONDS


@dataclass(frozen=True)
class GalleryImageDeliverySettings:
    cdn_origin: str
    cdn_token_secret: str = field(repr=False)
    imgproxy_key: str = field(repr=False)
    imgproxy_salt: str = field(repr=False)
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
        for name, value in (
            ("GALLERY_IMGPROXY_KEY", self.imgproxy_key),
            ("GALLERY_IMGPROXY_SALT", self.imgproxy_salt),
        ):
            if not isinstance(value, str) or re.fullmatch(r"(?:[0-9a-fA-F]{2})+", value) is None:
                raise ImproperlyConfigured(f"{name} must be nonempty hex-encoded bytes")
        if (
            not isinstance(self.bucket, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket) is None
            or ".." in self.bucket
        ):
            raise ImproperlyConfigured("PRIVATE_MEDIA_S3_BUCKET must be a valid bucket name")

    @classmethod
    def from_django_settings(cls) -> GalleryImageDeliverySettings:
        return cls(
            cdn_origin=getattr(settings, "GALLERY_CDN_ORIGIN", ""),
            cdn_token_secret=getattr(settings, "GALLERY_CDN_TOKEN_SECRET", ""),
            imgproxy_key=getattr(settings, "GALLERY_IMGPROXY_KEY", ""),
            imgproxy_salt=getattr(settings, "GALLERY_IMGPROXY_SALT", ""),
            bucket=getattr(settings, "PRIVATE_MEDIA_S3_BUCKET", ""),
        )


class GalleryImageUrlSigner:
    def __init__(
        self, config: GalleryImageDeliverySettings, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._config = config
        self._clock = clock

    def sign_accepted_preview(self, *, key: str, expires_in: int) -> str:
        """Sign an exact page-authorized preview without any storage or network calls."""
        validate_accepted_preview_key(key)
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or not 1 <= expires_in <= self._config.ttl_seconds
        ):
            raise ValueError("expires_in is outside the accepted preview limit")
        source = _urlsafe_base64(f"s3://{self._config.bucket}/{key}".encode())
        processing_path = f"/gallery-v1/{source}.jpg"
        signature = _imgproxy_signature(
            processing_path,
            key=bytes.fromhex(self._config.imgproxy_key),
            salt=bytes.fromhex(self._config.imgproxy_salt),
        )
        path = f"/{signature}{processing_path}"
        expires = int(self._clock()) + expires_in
        token = _cdn_token(path, expires=expires, secret=self._config.cdn_token_secret)
        return f"{self._config.cdn_origin.rstrip('/')}{path}?md5={token}&expires={expires}"


def _urlsafe_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _imgproxy_signature(path: str, *, key: bytes, salt: bytes) -> str:
    return _urlsafe_base64(hmac.digest(key, salt + path.encode(), "sha256"))


def _cdn_token(path: str, *, expires: int, secret: str) -> str:
    # Yandex CDN's secure-token protocol requires MD5, with no client-IP binding.
    return _urlsafe_base64(hashlib.md5(f"{expires}{path} {secret}".encode()).digest())
