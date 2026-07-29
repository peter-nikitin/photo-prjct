from __future__ import annotations

from pathlib import Path

import pytest
from photo_worker.metadata import InputTooLarge, MetadataError, extract_capture_metadata
from PIL import Image


def write_jpeg(
    path: Path,
    exif: dict[int, str] | None = None,
    *,
    size: tuple[int, int] = (8, 6),
) -> None:
    image = Image.new("RGB", size, "white")
    encoded = Image.Exif()
    for key, value in (exif or {}).items():
        encoded[key] = value
    image.save(path, "JPEG", exif=encoded)
    image.close()


def test_original_datetime_precedes_digitized_and_normalizes_explicit_offset(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(
        source,
        {
            36867: "2024:01:02 03:04:05",  # DateTimeOriginal
            36881: "+03:00",  # OffsetTimeOriginal
            36868: "2024:01:03 03:04:05",  # DateTimeDigitized
        },
    )

    result = extract_capture_metadata(source, max_bytes=1024 * 1024)

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.source_field == "DateTimeOriginal"
    assert result.timezone_state == "explicit"
    assert result.source_value == "2024:01:02 03:04:05"
    assert result.warnings == ("capture_time_conflicting",)


def test_missing_capture_time_is_a_successful_domain_result(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)

    result = extract_capture_metadata(source, max_bytes=1024 * 1024)

    assert result.capture_time is None
    assert result.source_field is None
    assert result.timezone_state == "not_applicable"
    assert result.source_value is None
    assert result.warnings == ("capture_time_missing",)


def test_malformed_higher_priority_field_falls_back_and_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source, {36867: "not-a-date", 306: "2024:02:03 04:05:06"})

    result = extract_capture_metadata(source, max_bytes=1024 * 1024)

    assert result.capture_time == "2024-02-03T04:05:06Z"
    assert result.source_field == "DateTime"
    assert result.timezone_state == "inferred_none"
    assert result.warnings == ("capture_time_malformed",)


def test_non_zero_padded_capture_time_is_malformed_not_a_submitable_success(tmp_path: Path) -> None:
    """Keep worker output within Django's canonical source-value grammar."""
    source = tmp_path / "photo.jpg"
    write_jpeg(source, {36867: "2026:7:9 1:2:3"})

    result = extract_capture_metadata(source, max_bytes=1024 * 1024)

    assert result.capture_time is None
    assert result.source_field is None
    assert result.timezone_state == "not_applicable"
    assert result.source_value is None
    assert result.warnings == ("capture_time_malformed", "capture_time_missing")


def test_unsupported_decode_failure_and_size_limit_have_stable_errors(tmp_path: Path) -> None:
    text_file = tmp_path / "not-a-jpeg.jpg"
    text_file.write_bytes(b"not an image")
    jpeg = tmp_path / "large.jpg"
    write_jpeg(jpeg)

    with pytest.raises(MetadataError, match="decode_failed"):
        extract_capture_metadata(text_file, max_bytes=1024)
    with pytest.raises(InputTooLarge, match="input_too_large"):
        extract_capture_metadata(jpeg, max_bytes=1)


def test_non_jpeg_image_has_the_permanent_unsupported_input_code(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    image = Image.new("RGB", (2, 2), "white")
    image.save(source, "PNG")
    image.close()

    with pytest.raises(MetadataError) as raised:
        extract_capture_metadata(source, max_bytes=1024)

    assert raised.value.code == "unsupported_input"


def test_pixel_limit_is_exact_and_orientation_does_not_change_exif_selection(
    tmp_path: Path,
) -> None:
    at_limit = tmp_path / "at-limit.jpg"
    over_limit = tmp_path / "over-limit.jpg"
    write_jpeg(
        at_limit,
        {36867: "2024:01:02 03:04:05", 274: 6},
        size=(3, 4),
    )
    write_jpeg(over_limit, size=(4, 4))

    result = extract_capture_metadata(at_limit, max_bytes=1024 * 1024, max_pixels=12)
    with pytest.raises(InputTooLarge, match="input_too_large"):
        extract_capture_metadata(over_limit, max_bytes=1024 * 1024, max_pixels=12)

    assert result.capture_time == "2024-01-02T03:04:05Z"
    assert result.source_field == "DateTimeOriginal"


@pytest.mark.parametrize("max_pixels", [0, 100_000_001])
def test_pixel_limit_must_be_positive_and_capped(tmp_path: Path, max_pixels: int) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)

    with pytest.raises(ValueError, match="max_pixels"):
        extract_capture_metadata(source, max_bytes=1024 * 1024, max_pixels=max_pixels)
