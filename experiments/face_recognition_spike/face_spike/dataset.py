from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from face_spike.domain import (
    DatasetError,
    DatasetItem,
    FaceExpected,
    ImageLimits,
    LoadedImage,
)

_LABEL_HEADER = ("filename", "participant_group", "face_expected")
_JPEG_EXTENSIONS = {".jpg", ".jpeg"}


def load_labels(path: Path, photo_root: Path) -> tuple[DatasetItem, ...]:
    root = photo_root.resolve()
    try:
        with path.open(newline="", encoding="utf-8") as labels_file:
            reader = csv.DictReader(labels_file)
            if reader.fieldnames != list(_LABEL_HEADER):
                raise DatasetError("invalid_labels")
            items = tuple(_parse_row(row, root) for row in reader)
    except DatasetError:
        raise
    except (OSError, UnicodeError, csv.Error):
        raise DatasetError("invalid_labels") from None

    if not items or len({item.filename for item in items}) != len(items):
        raise DatasetError("invalid_labels")
    return tuple(sorted(items, key=lambda item: item.filename))


def load_image(item: DatasetItem, photo_root: Path, limits: ImageLimits) -> LoadedImage:
    candidate = _resolve_candidate(item.filename, photo_root.resolve())
    if candidate.suffix.lower() not in _JPEG_EXTENSIONS:
        raise DatasetError("unsupported_image")
    if not candidate.is_file():
        raise DatasetError("invalid_labels")

    try:
        with Image.open(candidate) as verified_image:
            verified_image.verify()
        with Image.open(candidate) as source_image:
            width, height = source_image.size
            _validate_size(width, height, limits)
            oriented_image = ImageOps.exif_transpose(source_image)
            rgb_image = oriented_image.convert("RGB")
            rgb = np.ascontiguousarray(np.asarray(rgb_image, dtype=np.uint8))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise DatasetError("image_decode_failed") from None

    height, width = rgb.shape[:2]
    _validate_size(width, height, limits)
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    return LoadedImage(item=item, rgb=rgb, bgr=bgr, width=width, height=height)


def _parse_row(row: dict[str, str | None], root: Path) -> DatasetItem:
    if None in row or any(value is None for value in row.values()):
        raise DatasetError("invalid_labels")

    filename = row["filename"]
    participant_group = row["participant_group"].strip() or None
    try:
        face_expected = FaceExpected(row["face_expected"])
    except ValueError:
        raise DatasetError("invalid_labels") from None
    if face_expected is FaceExpected.YES and participant_group is None:
        raise DatasetError("invalid_labels")

    candidate = _resolve_candidate(filename, root)
    if Path(filename).suffix.lower() not in _JPEG_EXTENSIONS or not candidate.is_file():
        raise DatasetError("invalid_labels")
    return DatasetItem(
        filename=filename,
        participant_group=participant_group,
        face_expected=face_expected,
    )


def _resolve_candidate(filename: str, root: Path) -> Path:
    supplied = Path(filename)
    if not filename or supplied.is_absolute() or ".." in supplied.parts:
        raise DatasetError("unsafe_path")
    candidate = (root / supplied).resolve()
    if not candidate.is_relative_to(root):
        raise DatasetError("unsafe_path")
    return candidate


def _validate_size(width: int, height: int, limits: ImageLimits) -> None:
    if (
        width > limits.maximum_dimension
        or height > limits.maximum_dimension
        or width * height > limits.maximum_pixels
    ):
        raise DatasetError("image_too_large")
