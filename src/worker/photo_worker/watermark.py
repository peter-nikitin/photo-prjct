"""Bounded composition of the immutable public watermarked preview."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

if TYPE_CHECKING:
    from photo_worker.contracts import OutputSlot


ASSET_DIRECTORY = Path(__file__).with_name("assets")
MAX_WATERMARK_PIXELS_CAP = 24_000_000
LANDSCAPE_ASSET_NAME = "watermark-landscape-v1.png"
PORTRAIT_ASSET_NAME = "watermark-portrait-v1.png"
WATERMARK_ASSET_SHA256S = {
    "landscape": "adab8dcc93c744a79f8a33dc236f3da2e586b21a92070d902f4599dd27c161fa",
    "portrait": "d28f386783bed634eb55e7691217e434c697035108c24b7b8b066cb9af27b70a",
}


class WatermarkedPreviewError(ValueError):
    """A stable watermark-processing failure safe to return to Django."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WatermarkedPreviewResult:
    variant: str
    content_type: str
    byte_size: int
    width: int
    height: int
    sha256: str
    warnings: tuple[str, ...]

    def as_payload(self, *, upload_ms: int) -> dict[str, object]:
        return {
            "variant": self.variant,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "upload_ms": upload_ms,
            "warnings": list(self.warnings),
        }


def verify_packaged_watermark_assets(
    *,
    asset_directory: Path = ASSET_DIRECTORY,
    asset_sha256s: Mapping[str, str] = WATERMARK_ASSET_SHA256S,
) -> None:
    """Fail worker startup when the exact repository-owned overlay bytes are absent."""
    for orientation, filename in (
        ("landscape", LANDSCAPE_ASSET_NAME),
        ("portrait", PORTRAIT_ASSET_NAME),
    ):
        expected = asset_sha256s.get(orientation)
        if not isinstance(expected, str) or len(expected) != 64:
            raise RuntimeError("invalid declared watermark asset checksum")
        try:
            actual = _sha256(asset_directory / filename)
        except OSError as error:
            raise RuntimeError("packaged watermark asset is unavailable") from error
        if actual != expected:
            raise RuntimeError("packaged watermark asset checksum mismatch")


def generate_watermarked_preview(
    source: Path,
    destination: Path,
    *,
    max_input_bytes: int,
    max_pixels: int = MAX_WATERMARK_PIXELS_CAP,
    slot: OutputSlot,
    asset_directory: Path = ASSET_DIRECTORY,
    asset_sha256s: Mapping[str, str] = WATERMARK_ASSET_SHA256S,
) -> WatermarkedPreviewResult:
    """Overlay the fixed orientation-specific PNG onto an already oriented clean JPEG."""
    if max_input_bytes < 1 or not 1 <= max_pixels <= MAX_WATERMARK_PIXELS_CAP:
        raise ValueError("watermarked preview limits must be positive and capped")
    try:
        if source.stat().st_size > max_input_bytes:
            raise WatermarkedPreviewError("input_too_large")
    except WatermarkedPreviewError:
        raise
    except OSError as error:
        raise WatermarkedPreviewError("decode_failed") from error

    destination.unlink(missing_ok=True)
    warnings: list[str] = []
    try:
        with Image.open(source) as clean:
            if clean.format != "JPEG":
                raise WatermarkedPreviewError("unsupported_input")
            if clean.width < 1 or clean.height < 1:
                raise WatermarkedPreviewError("invalid_dimensions")
            if clean.width * clean.height > max_pixels:
                raise WatermarkedPreviewError("input_too_large")
            clean.load()
            width, height = clean.size
            normalized = _to_srgb(clean, warnings)
            try:
                overlay = _selected_overlay(
                    width,
                    height,
                    asset_directory=asset_directory,
                    asset_sha256s=asset_sha256s,
                )
                try:
                    fitted = ImageOps.fit(
                        overlay,
                        (width, height),
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                    try:
                        composed = normalized.convert("RGBA")
                        try:
                            composed.alpha_composite(fitted)
                            output = composed.convert("RGB")
                            try:
                                output.info.clear()
                                output.save(
                                    destination,
                                    format="JPEG",
                                    quality=85,
                                    icc_profile=_srgb_profile(),
                                )
                            finally:
                                output.close()
                        finally:
                            composed.close()
                    finally:
                        fitted.close()
                finally:
                    overlay.close()
            finally:
                normalized.close()
    except WatermarkedPreviewError:
        destination.unlink(missing_ok=True)
        raise
    except Image.DecompressionBombError as error:
        destination.unlink(missing_ok=True)
        raise WatermarkedPreviewError("input_too_large") from error
    except (UnidentifiedImageError, OSError) as error:
        destination.unlink(missing_ok=True)
        raise WatermarkedPreviewError("decode_failed") from error
    except (ImageCms.PyCMSError, TypeError, ValueError) as error:
        destination.unlink(missing_ok=True)
        raise WatermarkedPreviewError("normalization_failed") from error

    try:
        byte_size = destination.stat().st_size
        if byte_size < 1 or byte_size > slot.max_bytes:
            raise WatermarkedPreviewError("output_contract_violation")
        with Image.open(destination) as output:
            if (
                output.format != "JPEG"
                or output.size != (width, height)
                or output.width > slot.max_width
                or output.height > slot.max_height
            ):
                raise WatermarkedPreviewError("output_contract_violation")
        checksum = _sha256(destination)
    except WatermarkedPreviewError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise WatermarkedPreviewError("output_contract_violation") from error

    return WatermarkedPreviewResult(
        variant=slot.variant,
        content_type=slot.content_type,
        byte_size=byte_size,
        width=width,
        height=height,
        sha256=checksum,
        warnings=tuple(warnings),
    )


def _selected_overlay(
    width: int,
    height: int,
    *,
    asset_directory: Path,
    asset_sha256s: Mapping[str, str],
) -> Image.Image:
    orientation, filename = (
        ("landscape", LANDSCAPE_ASSET_NAME)
        if width >= height
        else ("portrait", PORTRAIT_ASSET_NAME)
    )
    expected = asset_sha256s.get(orientation)
    path = asset_directory / filename
    if not isinstance(expected, str) or _sha256_or_none(path) != expected:
        raise WatermarkedPreviewError("watermark_asset_mismatch")
    try:
        with Image.open(path) as opened:
            if (
                opened.format != "PNG"
                or opened.mode != "RGBA"
                or opened.width < 1
                or opened.height < 1
            ):
                raise WatermarkedPreviewError("watermark_asset_invalid")
            opened.load()
            return opened.copy()
    except WatermarkedPreviewError:
        raise
    except (UnidentifiedImageError, OSError) as error:
        raise WatermarkedPreviewError("watermark_asset_invalid") from error


def _sha256_or_none(path: Path) -> str | None:
    try:
        return _sha256(path)
    except OSError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _to_srgb(image: Image.Image, warnings: list[str]) -> Image.Image:
    profile = image.info.get("icc_profile")
    if profile is None:
        warnings.append("color_profile_missing")
        return image.convert("RGB")
    source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
    target_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    converted = ImageCms.profileToProfile(image, source_profile, target_profile, outputMode="RGB")
    if converted is None:
        raise WatermarkedPreviewError("normalization_failed")
    return converted


def _srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
