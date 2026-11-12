from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class NormalizedImage:
    content: bytes
    original_size: tuple[int, int]
    size: tuple[int, int]
    resize_scale: float


def normalize_image(content: bytes, *, long_edge: int = 1600) -> NormalizedImage:
    """Match deployed preview normalization: orientation, ICC-to-sRGB, no upscaling, JPEG-85."""
    if long_edge != 1600:
        raise ValueError("the frozen long edge is 1600")
    try:
        with Image.open(BytesIO(content)) as source:
            original_size = source.size
            oriented = ImageOps.exif_transpose(source)
            image = _to_srgb(oriented)
            scale = min(1.0, long_edge / max(image.size))
            size = tuple(round(side * scale) for side in image.size)
            if size != image.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            image.info.clear()
            encoded = BytesIO()
            image.save(encoded, format="JPEG", quality=85, icc_profile=_srgb_profile())
    except (ImageCms.PyCMSError, UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("unsupported image") from error
    return NormalizedImage(encoded.getvalue(), original_size, size, scale)


def _to_srgb(image: Image.Image) -> Image.Image:
    profile = image.info.get("icc_profile")
    if profile is None:
        return image.convert("RGB")
    return ImageCms.profileToProfile(
        image,
        ImageCms.ImageCmsProfile(BytesIO(profile)),
        ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")),
        outputMode="RGB",
    )


def _srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
