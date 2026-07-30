"""Bounded normalization of private JPEG originals into preview-small-v1."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from photo_worker.contracts import MAX_PREVIEW_PIXELS_CAP, OutputSlot


class PreviewError(ValueError):
    """A stable preview-processing failure safe to return to Django."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PreviewResult:
    variant: str
    content_type: str
    byte_size: int
    width: int
    height: int
    oriented_source_width: int
    oriented_source_height: int
    sha256: str
    warnings: tuple[str, ...]

    def as_payload(self, *, upload_ms: int) -> dict[str, object]:
        return {
            "variant": self.variant,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "oriented_source_width": self.oriented_source_width,
            "oriented_source_height": self.oriented_source_height,
            "sha256": self.sha256,
            "upload_ms": upload_ms,
            "warnings": list(self.warnings),
        }


def generate_preview(
    source: Path,
    destination: Path,
    *,
    max_input_bytes: int,
    max_pixels: int = MAX_PREVIEW_PIXELS_CAP,
    slot: OutputSlot,
) -> PreviewResult:
    """Create one JPEG preview while enforcing the input and output contract."""
    if max_input_bytes < 1 or not 1 <= max_pixels <= MAX_PREVIEW_PIXELS_CAP:
        raise ValueError("preview limits must be positive and capped")
    try:
        if source.stat().st_size > max_input_bytes:
            raise PreviewError("input_too_large")
    except PreviewError:
        raise
    except OSError as error:
        raise PreviewError("decode_failed") from error

    warnings: list[str] = []
    try:
        with Image.open(source) as opened:
            if opened.format != "JPEG":
                raise PreviewError("unsupported_input")
            if opened.width < 1 or opened.height < 1:
                raise PreviewError("invalid_dimensions")
            if opened.width * opened.height > max_pixels:
                raise PreviewError("input_too_large")
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                if oriented.width < 1 or oriented.height < 1:
                    raise PreviewError("invalid_dimensions")
                width, height = oriented.size
                normalized = _to_srgb(oriented, warnings)
                try:
                    preview = _resize(normalized, slot)
                    try:
                        destination.unlink(missing_ok=True)
                        preview.info.clear()
                        preview.save(
                            destination,
                            format="JPEG",
                            quality=85,
                            icc_profile=_srgb_profile(),
                        )
                    finally:
                        if preview is not normalized:
                            preview.close()
                finally:
                    normalized.close()
            finally:
                if oriented is not opened:
                    oriented.close()
    except PreviewError:
        destination.unlink(missing_ok=True)
        raise
    except Image.DecompressionBombError as error:
        destination.unlink(missing_ok=True)
        raise PreviewError("input_too_large") from error
    except (UnidentifiedImageError, OSError) as error:
        destination.unlink(missing_ok=True)
        raise PreviewError("decode_failed") from error
    except (ImageCms.PyCMSError, ValueError, TypeError) as error:
        destination.unlink(missing_ok=True)
        raise PreviewError("normalization_failed") from error
    try:
        byte_size = destination.stat().st_size
        if byte_size < 1 or byte_size > slot.max_bytes:
            raise PreviewError("output_contract_violation")
        with Image.open(destination) as preview:
            if (
                preview.format != "JPEG"
                or preview.width < 1
                or preview.height < 1
                or preview.width > slot.max_width
                or preview.height > slot.max_height
            ):
                raise PreviewError("output_contract_violation")
            output_width, output_height = preview.size
        checksum = _sha256(destination)
    except PreviewError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise PreviewError("output_contract_violation") from error

    return PreviewResult(
        variant=slot.variant,
        content_type=slot.content_type,
        byte_size=byte_size,
        width=output_width,
        height=output_height,
        oriented_source_width=width,
        oriented_source_height=height,
        sha256=checksum,
        warnings=tuple(warnings),
    )


def _to_srgb(image: Image.Image, warnings: list[str]) -> Image.Image:
    profile = image.info.get("icc_profile")
    if profile is None:
        warnings.append("color_profile_missing")
        converted = image.convert("RGB")
        if converted is None:
            raise PreviewError("normalization_failed")
        return converted
    source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
    target_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    profiled = ImageCms.profileToProfile(image, source_profile, target_profile, outputMode="RGB")
    if profiled is None:
        raise PreviewError("normalization_failed")
    return profiled


def _resize(image: Image.Image, slot: OutputSlot) -> Image.Image:
    long_edge = max(image.size)
    if long_edge <= 1600:
        return image
    scale = 1600 / long_edge
    dimensions = (round(image.width * scale), round(image.height * scale))
    if not all(dimension > 0 for dimension in dimensions):
        raise PreviewError("invalid_dimensions")
    return image.resize(dimensions, Image.Resampling.LANCZOS)


def _srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
