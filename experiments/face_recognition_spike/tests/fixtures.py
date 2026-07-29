from __future__ import annotations

from pathlib import Path

from PIL import Image


def write_json(path: Path, value: object) -> Path:
    """Write a UTF-8 JSON fixture with deterministic formatting."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def make_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = (3, 2),
    color: tuple[int, int, int] = (10, 20, 30),
    orientation: int | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    if orientation is None:
        image.save(path, "JPEG")
    else:
        exif = Image.Exif()
        exif[274] = orientation
        image.save(path, "JPEG", exif=exif)
    return path
