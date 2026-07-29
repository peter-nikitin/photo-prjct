"""Deterministic, bounded EXIF capture-time extraction."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from PIL import Image, UnidentifiedImageError

from photo_worker.contracts import MAX_PIXELS_CAP, CaptureMetadataResult

_FIELDS = (
    ("DateTimeOriginal", 36867, 36881),
    ("DateTimeDigitized", 36868, 36882),
    ("DateTime", 306, 36880),
)
_FIELDS_BY_NAME = {field[0]: field for field in _FIELDS}
_CANONICAL_EXIF_TIMESTAMP = re.compile(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")


class MetadataError(ValueError):
    def __init__(self, code: str = "decode_failed") -> None:
        super().__init__(code)
        self.code = code


class InputTooLarge(MetadataError):
    def __init__(self, code: str = "input_too_large") -> None:
        super().__init__(code)


def extract_capture_metadata(
    path: Path,
    *,
    max_bytes: int,
    max_pixels: int = MAX_PIXELS_CAP,
    date_field_precedence: tuple[str, ...] = tuple(_FIELDS_BY_NAME),
) -> CaptureMetadataResult:
    """Extract the configured v1 EXIF date precedence without decoding pixel arrays."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if not 1 <= max_pixels <= MAX_PIXELS_CAP:
        raise ValueError("max_pixels must be positive and capped")
    if tuple(date_field_precedence) != tuple(_FIELDS_BY_NAME):
        raise ValueError("unsupported EXIF field precedence")
    if path.stat().st_size > max_bytes:
        raise InputTooLarge("input_too_large")
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        # We inspect dimensions before asking Pillow for EXIF.  Disable Pillow's warning-based
        # threshold so the worker has one exact, deterministic max-pixel failure boundary.
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise MetadataError("unsupported_input")
            if image.width * image.height > max_pixels:
                raise InputTooLarge("input_too_large")
            exif = image.getexif()
            return _select_capture_time(cast(Mapping[int, object], exif), date_field_precedence)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise MetadataError("decode_failed") from error
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def _select_capture_time(
    exif: Mapping[int, object], date_field_precedence: tuple[str, ...]
) -> CaptureMetadataResult:
    warnings: list[str] = []
    candidates: list[tuple[str, str, datetime, bool]] = []
    for field in date_field_precedence:
        _, date_tag, offset_tag = _FIELDS_BY_NAME[field]
        raw_value = exif.get(date_tag)
        if raw_value is None:
            continue
        if not isinstance(raw_value, str) or _CANONICAL_EXIF_TIMESTAMP.fullmatch(raw_value) is None:
            _warn(warnings, "capture_time_malformed")
            continue
        try:
            parsed = datetime.strptime(raw_value, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            _warn(warnings, "capture_time_malformed")
            continue
        raw_offset = exif.get(offset_tag)
        if raw_offset is None:
            candidates.append((field, raw_value, parsed.replace(tzinfo=UTC), False))
            continue
        if not isinstance(raw_offset, str):
            _warn(warnings, "capture_time_malformed")
            candidates.append((field, raw_value, parsed.replace(tzinfo=UTC), False))
            continue
        try:
            offset = datetime.strptime(raw_offset, "%z").tzinfo
        except ValueError:
            _warn(warnings, "capture_time_malformed")
            candidates.append((field, raw_value, parsed.replace(tzinfo=UTC), False))
            continue
        if offset is None:
            _warn(warnings, "capture_time_malformed")
            candidates.append((field, raw_value, parsed.replace(tzinfo=UTC), False))
        else:
            candidates.append((field, raw_value, parsed.replace(tzinfo=offset), True))
    if not candidates:
        _warn(warnings, "capture_time_missing")
        return CaptureMetadataResult.missing(tuple(warnings))
    selected = candidates[0]
    if any(candidate[2] != selected[2] for candidate in candidates[1:]):
        _warn(warnings, "capture_time_conflicting")
    normalized = selected[2].astimezone(UTC).isoformat().replace("+00:00", "Z")
    return CaptureMetadataResult(
        capture_time=normalized,
        source_field=selected[0],
        timezone_state="explicit" if selected[3] else "inferred_none",
        source_value=selected[1],
        warnings=tuple(warnings),
    )


def _warn(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)
