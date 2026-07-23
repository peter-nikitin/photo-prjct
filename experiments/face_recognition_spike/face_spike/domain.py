from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import numpy as np
from numpy.typing import NDArray


class FaceExpected(StrEnum):
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class DatasetItem:
    filename: str
    participant_group: str | None
    face_expected: FaceExpected


@dataclass(frozen=True)
class ImageLimits:
    maximum_dimension: int
    maximum_pixels: int

    def __post_init__(self) -> None:
        if self.maximum_dimension < 1 or self.maximum_pixels < 1:
            raise ValueError("image limits must be positive")


@dataclass(frozen=True)
class LoadedImage:
    item: DatasetItem
    rgb: NDArray[np.uint8]
    bgr: NDArray[np.uint8]
    width: int
    height: int


ErrorCode = Literal[
    "invalid_labels",
    "unsafe_path",
    "image_decode_failed",
    "unsupported_image",
    "image_too_large",
]


class DatasetError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)
