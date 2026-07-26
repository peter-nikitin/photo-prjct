from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .analysis import DecodedImage, ImageProcessingError
from .inventory import EventPhoto


@dataclass(frozen=True)
class ImageLimits:
    maximum_dimension: int
    maximum_pixels: int

    def __post_init__(self) -> None:
        if self.maximum_dimension < 1 or self.maximum_pixels < 1:
            raise ValueError("image limits must be positive")


class PillowImageDecoder:
    def __init__(self, limits: ImageLimits) -> None:
        self._limits = limits

    def decode(self, photo: EventPhoto) -> DecodedImage:
        if photo.path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise ImageProcessingError("unsupported_image")
        try:
            with Image.open(photo.path) as verified:
                verified.verify()
            with Image.open(photo.path) as source:
                width, height = source.size
                self._validate_size(width, height)
                oriented = ImageOps.exif_transpose(source)
                rgb = np.ascontiguousarray(np.asarray(oriented.convert("RGB"), dtype=np.uint8))
        except ImageProcessingError:
            raise
        except Image.DecompressionBombError:
            raise ImageProcessingError("image_too_large") from None
        except (UnidentifiedImageError, OSError, ValueError):
            raise ImageProcessingError("image_decode_failed") from None

        height, width = rgb.shape[:2]
        self._validate_size(width, height)
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        return DecodedImage(rgb=rgb, bgr=bgr, width=width, height=height)

    def _validate_size(self, width: int, height: int) -> None:
        if (
            width < 1
            or height < 1
            or width > self._limits.maximum_dimension
            or height > self._limits.maximum_dimension
            or width * height > self._limits.maximum_pixels
        ):
            raise ImageProcessingError("image_too_large")
