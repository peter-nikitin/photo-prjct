from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

InventoryErrorCode = Literal["invalid_photo_inventory"]
_JPEG_EXTENSIONS = {".jpg", ".jpeg"}


class InventoryError(Exception):
    def __init__(self, code: InventoryErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EventPhoto:
    filename: str
    path: Path


@dataclass(frozen=True)
class EventPhotoInventory:
    photos: tuple[EventPhoto, ...]


def load_event_photo_inventory(photo_root: Path) -> EventPhotoInventory:
    """Load a deterministic, direct-only inventory of event JPEG originals."""
    try:
        root = photo_root.resolve()
        if not root.is_dir():
            raise InventoryError("invalid_photo_inventory")
        entries = tuple(root.iterdir())
    except InventoryError:
        raise
    except OSError:
        raise InventoryError("invalid_photo_inventory") from None

    photos: list[EventPhoto] = []
    for entry in entries:
        if entry.is_dir():
            raise InventoryError("invalid_photo_inventory")
        if entry.suffix.lower() not in _JPEG_EXTENSIONS:
            continue
        if entry.is_symlink() or not entry.is_file():
            raise InventoryError("invalid_photo_inventory")
        photos.append(EventPhoto(entry.name, entry))

    filenames = [photo.filename for photo in photos]
    if not filenames or len({filename.casefold() for filename in filenames}) != len(filenames):
        raise InventoryError("invalid_photo_inventory")
    return EventPhotoInventory(tuple(sorted(photos, key=lambda photo: photo.filename)))
