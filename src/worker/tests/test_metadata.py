from __future__ import annotations

from pathlib import Path

import pytest
from photo_worker.metadata import InputTooLarge, MetadataError, extract_capture_metadata
from PIL import ExifTags, Image

FIXTURE = Path(__file__).parent / "fixtures" / "capture-time-nested.mpo"
MOSCOW = "Europe/Moscow"


def write_jpeg(
    path: Path,
    root_exif: dict[int, str] | None = None,
    *,
    nested_exif: dict[int, str] | None = None,
    size: tuple[int, int] = (8, 6),
) -> None:
    image = Image.new("RGB", size, "white")
    encoded = Image.Exif()
    for key, value in (root_exif or {}).items():
        encoded[key] = value
    encoded.get_ifd(ExifTags.IFD.Exif).update(nested_exif or {})
    try:
        image.save(path, "JPEG", exif=encoded)
    finally:
        image.close()


def test_nested_mpo_fixture_is_synthetic_and_contains_only_capture_metadata() -> None:
    """A real fixture catches regressions in Pillow's nested MPO EXIF traversal."""
    with Image.open(FIXTURE) as image:
        exif = image.getexif()
        nested = exif.get_ifd(ExifTags.IFD.Exif)

        assert image.format == "MPO"
        assert image.n_frames == 2
        assert image.size == (2, 2)
        assert set(exif) == {34665}  # Exif IFD pointer only; no GPS/root identity fields.
        assert dict(nested) == {
            36867: "2024:01:02 03:04:05",
            36881: "+03:00",
        }
        assert 34853 not in exif and 34853 not in nested  # GPSInfo
        for frame in range(image.n_frames):
            image.seek(frame)
            assert set(image.convert("RGB").getdata()) == {(255, 255, 255)}


def test_nested_mpo_original_time_with_explicit_offset_normalizes_to_utc() -> None:
    result = extract_capture_metadata(FIXTURE, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.source_field == "DateTimeOriginal"
    assert result.timezone_state == "explicit"
    assert result.source_value == "2024:01:02 03:04:05"
    assert result.source_offset == "+03:00"
    assert result.event_timezone == MOSCOW
    assert result.warnings == ()


def test_offsetless_jpeg_uses_the_configured_event_timezone(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source, nested_exif={36867: "2024:01:02 03:04:05"})

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.source_field == "DateTimeOriginal"
    assert result.timezone_state == "event_timezone"
    assert result.source_value == "2024:01:02 03:04:05"
    assert result.source_offset is None
    assert result.event_timezone == MOSCOW
    assert result.warnings == ()


def test_root_datetime_uses_its_nested_exif_offset_not_the_event_timezone(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(
        source,
        {306: "2024:01:02 03:04:05"},
        nested_exif={36880: "+01:00"},
    )

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-01-02T02:04:05Z"
    assert result.source_field == "DateTime"
    assert result.timezone_state == "explicit"
    assert result.source_offset == "+01:00"
    assert result.warnings == ()


def test_nested_time_wins_over_a_conflicting_root_duplicate(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(
        source,
        {36867: "2024:01:02 04:04:05", 36881: "+03:00"},
        nested_exif={36867: "2024:01:02 03:04:05", 36881: "+03:00"},
    )

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.source_value == "2024:01:02 03:04:05"
    assert result.source_offset == "+03:00"
    assert result.warnings == ("capture_time_conflicting",)


def test_malformed_offset_falls_back_to_event_timezone_and_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(
        source,
        nested_exif={36867: "2024:01:02 03:04:05", 36881: "+99:00"},
    )

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.timezone_state == "event_timezone"
    assert result.source_offset is None
    assert result.warnings == ("capture_time_malformed_offset",)


def test_equal_instants_with_different_offsets_do_not_conflict(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(
        source,
        nested_exif={
            36867: "2024:01:02 03:04:05",
            36881: "+03:00",
            36868: "2024:01:02 00:04:05",
            36882: "+00:00",
        },
    )

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.source_field == "DateTimeOriginal"
    assert result.warnings == ()


def test_malformed_higher_priority_field_falls_back_and_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(
        source,
        {306: "2024:02:03 04:05:06"},
        nested_exif={36867: "not-a-date"},
    )

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time == "2024-02-03T01:05:06Z"
    assert result.source_field == "DateTime"
    assert result.timezone_state == "event_timezone"
    assert result.warnings == ("capture_time_malformed",)


def test_missing_capture_time_is_a_successful_domain_result(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time is None
    assert result.source_field is None
    assert result.timezone_state == "not_applicable"
    assert result.source_value is None
    assert result.source_offset is None
    assert result.event_timezone == MOSCOW
    assert result.warnings == ("capture_time_missing",)


def test_non_zero_padded_capture_time_is_malformed_not_a_submitable_success(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source, nested_exif={36867: "2026:7:9 1:2:3"})

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone=MOSCOW)

    assert result.capture_time is None
    assert result.source_field is None
    assert result.timezone_state == "not_applicable"
    assert result.source_value is None
    assert result.warnings == ("capture_time_malformed", "capture_time_missing")


@pytest.mark.parametrize("wall_time", ["2024:03:31 02:30:00", "2024:10:27 02:30:00"])
def test_nonexistent_or_ambiguous_event_wall_time_is_not_guessed(
    tmp_path: Path, wall_time: str
) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source, nested_exif={36867: wall_time})

    result = extract_capture_metadata(source, max_bytes=1024 * 1024, event_timezone="Europe/Berlin")

    assert result.capture_time is None
    assert result.timezone_state == "not_applicable"
    assert result.event_timezone == "Europe/Berlin"
    assert result.warnings == ("capture_time_timezone_ambiguous", "capture_time_missing")


def test_unsupported_decode_failure_and_size_limit_have_stable_errors(tmp_path: Path) -> None:
    text_file = tmp_path / "not-an-image.jpg"
    text_file.write_bytes(b"not an image")
    jpeg = tmp_path / "large.jpg"
    write_jpeg(jpeg)

    with pytest.raises(MetadataError, match="decode_failed"):
        extract_capture_metadata(text_file, max_bytes=1024, event_timezone=MOSCOW)
    with pytest.raises(InputTooLarge, match="input_too_large"):
        extract_capture_metadata(jpeg, max_bytes=1, event_timezone=MOSCOW)


def test_other_decoded_image_has_the_permanent_unsupported_input_code(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    image = Image.new("RGB", (2, 2), "white")
    image.save(source, "PNG")
    image.close()

    with pytest.raises(MetadataError) as raised:
        extract_capture_metadata(source, max_bytes=1024, event_timezone=MOSCOW)

    assert raised.value.code == "unsupported_input"


def test_pixel_limit_is_exact_and_orientation_does_not_change_exif_selection(
    tmp_path: Path,
) -> None:
    at_limit = tmp_path / "at-limit.jpg"
    over_limit = tmp_path / "over-limit.jpg"
    write_jpeg(
        at_limit,
        {274: 6},
        nested_exif={36867: "2024:01:02 03:04:05"},
        size=(3, 4),
    )
    write_jpeg(over_limit, size=(4, 4))

    result = extract_capture_metadata(
        at_limit, max_bytes=1024 * 1024, max_pixels=12, event_timezone=MOSCOW
    )
    with pytest.raises(InputTooLarge, match="input_too_large"):
        extract_capture_metadata(
            over_limit, max_bytes=1024 * 1024, max_pixels=12, event_timezone=MOSCOW
        )

    assert result.capture_time == "2024-01-02T00:04:05Z"
    assert result.source_field == "DateTimeOriginal"


@pytest.mark.parametrize("max_pixels", [0, 100_000_001])
def test_pixel_limit_must_be_positive_and_capped(tmp_path: Path, max_pixels: int) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)

    with pytest.raises(ValueError, match="max_pixels"):
        extract_capture_metadata(
            source, max_bytes=1024 * 1024, max_pixels=max_pixels, event_timezone=MOSCOW
        )
