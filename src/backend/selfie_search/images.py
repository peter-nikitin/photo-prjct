from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Literal, cast

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()

PreparedContentType = Literal["image/jpeg", "image/png"]
PreparedSourceFormat = Literal["jpeg", "png", "heic", "heif"]
SelfieRejectionReason = Literal[
    "missing_or_empty",
    "unsupported_format",
    "corrupt_image",
    "source_too_large",
    "normalized_too_large",
    "pixel_limit_exceeded",
]

_SUPPORTED_FORMATS = {"jpeg", "png", "heic", "heif"}
_SUPPORTED_DECLARED_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif"}


@dataclass(frozen=True)
class PreparedSelfie:
    content: bytes
    content_type: PreparedContentType
    source_size: int
    source_format: PreparedSourceFormat


class SelfieImageRejected(ValueError):
    def __init__(self, reason: SelfieRejectionReason, actual_format: str | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.actual_format = actual_format


def prepare_selfie_image(upload: UploadedFile) -> PreparedSelfie:
    source_size = _upload_size(upload)
    maximum = settings.SELFIE_SEARCH_MAX_UPLOAD_BYTES
    if source_size <= 0:
        raise SelfieImageRejected("missing_or_empty", None)
    if source_size > maximum:
        raise SelfieImageRejected("source_too_large", None)

    content = upload.read(maximum)
    upload.seek(0)
    if not isinstance(content, bytes) or not content:
        raise SelfieImageRejected("missing_or_empty", None)
    source_size = len(content)
    if source_size > maximum:
        raise SelfieImageRejected("source_too_large", None)

    try:
        with Image.open(BytesIO(content)) as image:
            actual_format = _actual_format(image)
            if actual_format not in _SUPPORTED_FORMATS:
                raise SelfieImageRejected("unsupported_format", actual_format)
            _check_pixel_limit(image)
            if actual_format in {"jpeg", "png"}:
                image.verify()
                with Image.open(BytesIO(content)) as verified:
                    _check_pixel_limit(verified)
                    verified.load()
                return PreparedSelfie(
                    content=content,
                    content_type=cast(PreparedContentType, f"image/{actual_format}"),
                    source_size=source_size,
                    source_format=cast(PreparedSourceFormat, actual_format),
                )

            image.load()
            normalized = _normalize_heif(image)
            if len(normalized) > maximum:
                raise SelfieImageRejected("normalized_too_large", actual_format)
            return PreparedSelfie(
                content=normalized,
                content_type="image/jpeg",
                source_size=source_size,
                source_format=cast(PreparedSourceFormat, actual_format),
            )
    except SelfieImageRejected:
        raise
    except Image.DecompressionBombError:
        raise SelfieImageRejected("pixel_limit_exceeded", None) from None
    except (UnidentifiedImageError, EOFError, OSError, SyntaxError, ValueError, RuntimeError):
        reason: SelfieRejectionReason = (
            "corrupt_image"
            if _declared_content_type(upload) in _SUPPORTED_DECLARED_TYPES
            else "unsupported_format"
        )
        raise SelfieImageRejected(reason, None) from None


def _upload_size(upload: UploadedFile) -> int:
    size = getattr(upload, "size", 0)
    return size if isinstance(size, int) and not isinstance(size, bool) else 0


def _declared_content_type(upload: UploadedFile) -> str | None:
    declared = getattr(upload, "content_type", None)
    return declared if isinstance(declared, str) else None


def _actual_format(image: Image.Image) -> str | None:
    image_format = image.format
    if image_format == "JPEG":
        return "jpeg"
    if image_format == "PNG":
        return "png"
    if image_format == "HEIF":
        mimetype = getattr(image, "custom_mimetype", None)
        if mimetype not in {"image/heic", "image/heif"}:
            mimetype = getattr(image, "get_format_mimetype", lambda: None)()
        if mimetype == "image/heic":
            return "heic"
        return "heif"
    return image_format.lower() if isinstance(image_format, str) else None


def _check_pixel_limit(image: Image.Image) -> None:
    width, height = image.size
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width * height > settings.SELFIE_SEARCH_MAX_PIXELS
    ):
        raise SelfieImageRejected("pixel_limit_exceeded", _actual_format(image))


def _normalize_heif(image: Image.Image) -> bytes:
    upright = ImageOps.exif_transpose(image)
    rgba = upright.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    try:
        background.alpha_composite(rgba)
        rgb = background.convert("RGB")
        try:
            output = BytesIO()
            rgb.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue()
        finally:
            rgb.close()
    finally:
        rgba.close()
        background.close()
        if upright is not image:
            upright.close()
