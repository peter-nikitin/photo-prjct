"""Deterministic, bounded EXIF capture-time extraction."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import ExifTags, Image, UnidentifiedImageError

from photo_worker.contracts import MAX_PIXELS_CAP, CaptureMetadataResult

_FIELDS = (
    ("DateTimeOriginal", 36867, 36881),
    ("DateTimeDigitized", 36868, 36882),
    ("DateTime", 306, 36880),
)
_FIELDS_BY_NAME = {field[0]: field for field in _FIELDS}
_CANONICAL_EXIF_TIMESTAMP = re.compile(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")
_CANONICAL_EXIF_OFFSET = re.compile(r"[+-]\d{2}:\d{2}")
_EXIF_IFD = ExifTags.IFD.Exif


class MetadataError(ValueError):
    def __init__(self, code: str = "decode_failed") -> None:
        super().__init__(code)
        self.code = code


class InputTooLarge(MetadataError):
    def __init__(self, code: str = "input_too_large") -> None:
        super().__init__(code)


@dataclass(frozen=True)
class _CaptureCandidate:
    field: str
    source_value: str
    source_offset: str | None
    instant: datetime
    timezone_state: str


def extract_capture_metadata(
    path: Path,
    *,
    max_bytes: int,
    max_pixels: int = MAX_PIXELS_CAP,
    date_field_precedence: tuple[str, ...] = tuple(_FIELDS_BY_NAME),
    event_timezone: str,
) -> CaptureMetadataResult:
    """Extract JPEG/MPO capture time without decoding pixels or enumerating MPO frames."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if not 1 <= max_pixels <= MAX_PIXELS_CAP:
        raise ValueError("max_pixels must be positive and capped")
    if tuple(date_field_precedence) != tuple(_FIELDS_BY_NAME):
        raise ValueError("unsupported EXIF field precedence")
    try:
        zone = ZoneInfo(event_timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("event_timezone must be a valid IANA timezone") from error
    if path.stat().st_size > max_bytes:
        raise InputTooLarge("input_too_large")

    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        # Dimensions are read from the primary image header.  Pixels and additional MPO frames
        # remain untouched, keeping this path within the declared input bounds.
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as image:
            if image.format not in {"JPEG", "MPO"}:
                raise MetadataError("unsupported_input")
            if image.width * image.height > max_pixels:
                raise InputTooLarge("input_too_large")
            root = cast(Mapping[int, object], image.getexif())
            nested = _nested_exif(root)
            return _select_capture_time(root, nested, date_field_precedence, zone, event_timezone)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise MetadataError("decode_failed") from error
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def _nested_exif(exif: Mapping[int, object]) -> Mapping[int, object]:
    getter = getattr(exif, "get_ifd", None)
    if getter is None:
        return {}
    try:
        nested = getter(_EXIF_IFD)
    except (KeyError, OSError, ValueError):
        return {}
    return cast(Mapping[int, object], nested) if isinstance(nested, Mapping) else {}


def _select_capture_time(
    root: Mapping[int, object],
    nested: Mapping[int, object],
    date_field_precedence: tuple[str, ...],
    event_zone: ZoneInfo,
    event_timezone: str,
) -> CaptureMetadataResult:
    warnings: list[str] = []
    candidates: list[_CaptureCandidate] = []
    for field in date_field_precedence:
        _, date_tag, offset_tag = _FIELDS_BY_NAME[field]
        primary_date_values = nested if field != "DateTime" else root
        primary_offset_values = nested
        fallback = root if field != "DateTime" else None
        _append_candidate(
            candidates,
            warnings,
            field,
            primary_date_values,
            primary_offset_values,
            date_tag,
            offset_tag,
            event_zone,
        )
        # Pillow may expose a duplicate nested date tag at the root.  It can contribute only as a
        # lower-priority comparison/fallback source for that same standard EXIF field.
        if fallback is not None and fallback.get(date_tag) is not None:
            _append_candidate(
                candidates,
                warnings,
                field,
                fallback,
                fallback,
                date_tag,
                offset_tag,
                event_zone,
            )

    if not candidates:
        _warn(warnings, "capture_time_missing")
        return CaptureMetadataResult.missing(tuple(warnings), event_timezone=event_timezone)

    selected = candidates[0]
    if any(candidate.instant != selected.instant for candidate in candidates[1:]):
        _warn(warnings, "capture_time_conflicting")
    return CaptureMetadataResult(
        capture_time=selected.instant.isoformat().replace("+00:00", "Z"),
        source_field=selected.field,
        timezone_state=selected.timezone_state,
        source_value=selected.source_value,
        source_offset=selected.source_offset,
        event_timezone=event_timezone,
        warnings=tuple(warnings),
    )


def _append_candidate(
    candidates: list[_CaptureCandidate],
    warnings: list[str],
    field: str,
    date_values: Mapping[int, object],
    offset_values: Mapping[int, object],
    date_tag: int,
    offset_tag: int,
    event_zone: ZoneInfo,
) -> None:
    raw_value = date_values.get(date_tag)
    if raw_value is None:
        return
    if not isinstance(raw_value, str) or _CANONICAL_EXIF_TIMESTAMP.fullmatch(raw_value) is None:
        _warn(warnings, "capture_time_malformed")
        return
    try:
        wall_time = datetime.strptime(raw_value, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        _warn(warnings, "capture_time_malformed")
        return

    raw_offset = offset_values.get(offset_tag)
    if raw_offset is not None:
        if isinstance(raw_offset, str) and _CANONICAL_EXIF_OFFSET.fullmatch(raw_offset) is not None:
            try:
                offset = datetime.strptime(raw_offset, "%z").tzinfo
            except ValueError:
                offset = None
            if offset is not None:
                candidates.append(
                    _CaptureCandidate(
                        field,
                        raw_value,
                        raw_offset,
                        wall_time.replace(tzinfo=offset).astimezone(UTC),
                        "explicit",
                    )
                )
                return
        _warn(warnings, "capture_time_malformed_offset")

    instant = _event_wall_time_to_utc(wall_time, event_zone)
    if instant is None:
        _warn(warnings, "capture_time_timezone_ambiguous")
        return
    candidates.append(_CaptureCandidate(field, raw_value, None, instant, "event_timezone"))


def _event_wall_time_to_utc(wall_time: datetime, event_zone: ZoneInfo) -> datetime | None:
    first = wall_time.replace(tzinfo=event_zone, fold=0)
    second = wall_time.replace(tzinfo=event_zone, fold=1)
    first_round_trip = first.astimezone(UTC).astimezone(event_zone).replace(tzinfo=None)
    second_round_trip = second.astimezone(UTC).astimezone(event_zone).replace(tzinfo=None)
    if (
        first_round_trip != wall_time
        or second_round_trip != wall_time
        or first.utcoffset() != second.utcoffset()
    ):
        return None
    return first.astimezone(UTC)


def _warn(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)
