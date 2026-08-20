from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from photo_worker.contracts import OutputSlot
from photo_worker.preview import PreviewError, generate_preview
from PIL import Image, ImageCms


def slot(**overrides: object) -> OutputSlot:
    values: dict[str, object] = {
        "variant": "preview-small-v1",
        "upload_url": "https://storage.example.test/put?signature=secret",
        "upload_expires_at": "2026-07-30T10:00:00Z",
        "content_type": "image/jpeg",
        "staging_key": "processing-pending/previews/attempt/preview-small-v1.jpg",
        "max_bytes": 10_485_760,
        "max_width": 1600,
        "max_height": 1600,
        "checksum_algorithm": "sha256",
    }
    values.update(overrides)
    return OutputSlot(**values)  # type: ignore[arg-type]


def jpeg(path: Path, size: tuple[int, int], *, orientation: int | None = None) -> None:
    image = Image.new("RGB", size, "red")
    try:
        exif = Image.Exif()
        if orientation is not None:
            exif[274] = orientation
        image.save(path, "JPEG", exif=exif)
    finally:
        image.close()


@pytest.mark.parametrize(
    ("source_size", "expected"),
    [((3200, 2000), (1600, 1000)), ((2000, 3200), (1000, 1600)), ((4000, 3000), (1600, 1200))],
)
def test_generate_preview_scales_long_edge_without_distorting_aspect_ratio(
    tmp_path: Path, source_size: tuple[int, int], expected: tuple[int, int]
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, source_size)

    result = generate_preview(
        source, output, max_input_bytes=10_000_000, max_pixels=20_000_000, slot=slot()
    )

    with Image.open(output) as image:
        assert image.size == expected
    assert (result.width, result.height) == expected
    assert (result.oriented_source_width, result.oriented_source_height) == source_size


def test_generate_preview_does_not_enlarge_small_image(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (640, 400))

    result = generate_preview(
        source, output, max_input_bytes=10_000_000, max_pixels=1_000_000, slot=slot()
    )

    assert (result.width, result.height) == (640, 400)


def test_generate_preview_accepts_jpeg_with_mpf_metadata_reported_as_mpo(tmp_path: Path) -> None:
    source = tmp_path / "canon-eos-r6m2.jpg"
    output = tmp_path / "preview.jpg"
    first = Image.new("RGB", (640, 400), "red")
    second = Image.new("RGB", (640, 400), "blue")
    try:
        first.save(source, format="MPO", save_all=True, append_images=[second])
    finally:
        first.close()
        second.close()

    with Image.open(source) as opened:
        assert opened.format == "MPO"

    result = generate_preview(
        source, output, max_input_bytes=10_000_000, max_pixels=1_000_000, slot=slot()
    )

    assert (result.width, result.height) == (640, 400)
    with Image.open(output) as preview:
        assert preview.format == "JPEG"


@pytest.mark.parametrize(
    ("orientation", "expected"),
    [
        (1, (40, 20)),
        (2, (40, 20)),
        (3, (40, 20)),
        (4, (40, 20)),
        (5, (20, 40)),
        (6, (20, 40)),
        (7, (20, 40)),
        (8, (20, 40)),
    ],
)
def test_generate_preview_applies_each_exif_orientation_to_pixels(
    tmp_path: Path, orientation: int, expected: tuple[int, int]
) -> None:
    source = tmp_path / f"source-{orientation}.jpg"
    output = tmp_path / f"preview-{orientation}.jpg"
    jpeg(source, (40, 20), orientation=orientation)

    result = generate_preview(
        source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot()
    )

    assert (result.width, result.height) == expected
    assert (result.oriented_source_width, result.oriented_source_height) == expected


_CORNERS = {
    "top_left": (245, 20, 20),
    "top_right": (20, 225, 35),
    "bottom_left": (25, 45, 240),
    "bottom_right": (235, 220, 20),
}
_ORIENTED_CORNERS = {
    1: ("top_left", "top_right", "bottom_left", "bottom_right"),
    2: ("top_right", "top_left", "bottom_right", "bottom_left"),
    3: ("bottom_right", "bottom_left", "top_right", "top_left"),
    4: ("bottom_left", "bottom_right", "top_left", "top_right"),
    5: ("top_left", "bottom_left", "top_right", "bottom_right"),
    6: ("bottom_left", "top_left", "bottom_right", "top_right"),
    7: ("bottom_right", "top_right", "bottom_left", "top_left"),
    8: ("top_right", "bottom_right", "top_left", "bottom_left"),
}


def _quadrant_jpeg(path: Path, *, orientation: int) -> None:
    image = Image.new("RGB", (80, 60))
    try:
        for corner, color in _CORNERS.items():
            left = 0 if "left" in corner else 40
            top = 0 if "top" in corner else 30
            image.paste(color, (left, top, left + 40, top + 30))
        exif = Image.Exif()
        exif[274] = orientation
        image.save(path, "JPEG", quality=100, subsampling=0, exif=exif)
    finally:
        image.close()


def _assert_close_rgb(actual: tuple[int, int, int], expected: tuple[int, int, int]) -> None:
    assert all(
        abs(current - wanted) <= 35 for current, wanted in zip(actual, expected, strict=True)
    )


@pytest.mark.parametrize("orientation", range(1, 9))
def test_generate_preview_applies_each_exif_orientation_to_asymmetric_pixels(
    tmp_path: Path, orientation: int
) -> None:
    source = tmp_path / f"quadrants-{orientation}.jpg"
    output = tmp_path / f"preview-{orientation}.jpg"
    _quadrant_jpeg(source, orientation=orientation)

    generate_preview(source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot())

    with Image.open(output) as preview:
        width, height = preview.size
        points = ((10, 10), (width - 10, 10), (10, height - 10), (width - 10, height - 10))
        for point, expected_corner in zip(points, _ORIENTED_CORNERS[orientation], strict=True):
            _assert_close_rgb(preview.getpixel(point), _CORNERS[expected_corner])


def test_generate_preview_converts_embedded_profile_to_srgb(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    image = Image.new("RGB", (32, 24), "red")
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    try:
        image.save(source, "JPEG", icc_profile=profile)
    finally:
        image.close()

    generate_preview(source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot())

    with Image.open(output) as preview:
        output_profile = preview.info["icc_profile"]
    assert "sRGB" in ImageCms.getProfileName(ImageCms.ImageCmsProfile(io.BytesIO(output_profile)))


def test_generate_preview_applies_icc_transform_to_pixel_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    image = Image.new("RGB", (32, 24), (230, 30, 20))
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    try:
        image.save(source, "JPEG", quality=100, subsampling=0, icc_profile=profile)
    finally:
        image.close()

    def controlled_transform(
        source_image: Image.Image, *_profiles: object, **_kwargs: object
    ) -> Image.Image:
        assert source_image.info["icc_profile"] == profile
        return Image.new("RGB", source_image.size, (25, 180, 70))

    monkeypatch.setattr("photo_worker.preview.ImageCms.profileToProfile", controlled_transform)

    generate_preview(source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot())

    with Image.open(output) as preview:
        _assert_close_rgb(preview.getpixel((16, 12)), (25, 180, 70))


def test_generate_preview_uses_declared_srgb_default_when_profile_is_absent(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (32, 24))

    result = generate_preview(
        source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot()
    )

    assert result.warnings == ("color_profile_missing",)
    with Image.open(output) as preview:
        assert "sRGB" in ImageCms.getProfileName(
            ImageCms.ImageCmsProfile(io.BytesIO(preview.info["icc_profile"]))
        )


def test_generate_preview_uses_configured_jpeg_quality_and_strips_source_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    image = Image.new("RGB", (101, 67), "red")
    exif = Image.Exif()
    exif[270] = "hostile source comment"
    try:
        image.save(source, "JPEG", quality=12, comment=b"secret", exif=exif)
    finally:
        image.close()

    result = generate_preview(
        source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot()
    )

    assert result.byte_size == output.stat().st_size
    assert result.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    with Image.open(output) as preview:
        assert preview.getexif() == {}
        assert "comment" not in preview.info
        assert preview.quantization[0][0] == 5


@pytest.mark.parametrize(
    ("source_bytes", "max_bytes", "code"),
    [(b"x" * 100, 99, "input_too_large"), (b"not-a-jpeg", 100, "decode_failed")],
)
def test_generate_preview_rejects_invalid_or_excessive_input(
    tmp_path: Path, source_bytes: bytes, max_bytes: int, code: str
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    source.write_bytes(source_bytes)

    with pytest.raises(PreviewError, match=code):
        generate_preview(source, output, max_input_bytes=max_bytes, max_pixels=10_000, slot=slot())

    assert not output.exists()


def test_generate_preview_rejects_excessive_pixels(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (101, 100))

    with pytest.raises(PreviewError, match="input_too_large"):
        generate_preview(source, output, max_input_bytes=10_000_000, max_pixels=10_000, slot=slot())

    assert not output.exists()


def test_generate_preview_accepts_exact_runtime_pixel_bound_and_rejects_one_over(
    tmp_path: Path,
) -> None:
    at_limit = tmp_path / "at-limit.jpg"
    over_limit = tmp_path / "over-limit.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(at_limit, (12, 10))
    jpeg(over_limit, (11, 11))

    result = generate_preview(
        at_limit,
        output,
        max_input_bytes=10_000,
        max_pixels=120,
        slot=slot(),
    )
    with pytest.raises(PreviewError, match="input_too_large"):
        generate_preview(
            over_limit,
            output,
            max_input_bytes=10_000,
            max_pixels=120,
            slot=slot(),
        )

    assert (result.width, result.height) == (12, 10)
    assert not output.exists()


def test_generate_preview_rejects_declared_limit_above_preview_memory_cap(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (10, 10))

    with pytest.raises(ValueError, match="preview limits"):
        generate_preview(
            source,
            output,
            max_input_bytes=10_000,
            max_pixels=24_000_001,
            slot=slot(),
        )


def test_generate_preview_does_not_disable_pillow_global_decompression_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (10, 10))
    configured_limit = Image.MAX_IMAGE_PIXELS
    observed_limits: list[int | None] = []
    real_open = Image.open

    def observing_open(*args: object, **kwargs: object):
        observed_limits.append(Image.MAX_IMAGE_PIXELS)
        return real_open(*args, **kwargs)

    monkeypatch.setattr("photo_worker.preview.Image.open", observing_open)

    generate_preview(source, output, max_input_bytes=10_000, max_pixels=100, slot=slot())

    assert observed_limits
    assert all(limit == configured_limit for limit in observed_limits)


def test_generate_preview_maps_pillow_decompression_bomb_to_input_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    source.write_bytes(b"jpeg-header")
    monkeypatch.setattr(
        "photo_worker.preview.Image.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            Image.DecompressionBombError("hostile dimensions")
        ),
    )

    with pytest.raises(PreviewError, match="input_too_large"):
        generate_preview(source, output, max_input_bytes=10_000, max_pixels=100, slot=slot())

    assert not output.exists()


def test_generate_preview_rejects_invalid_source_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    source.write_bytes(b"jpeg")

    class InvalidImage:
        format = "JPEG"
        width = 0
        height = 1

        def __enter__(self) -> InvalidImage:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("photo_worker.preview.Image.open", lambda _: InvalidImage())

    with pytest.raises(PreviewError, match="invalid_dimensions"):
        generate_preview(source, output, max_input_bytes=10_000, max_pixels=10_000, slot=slot())

    assert not output.exists()


def test_generate_preview_maps_unsupported_color_conversion_to_stable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (10, 10))

    monkeypatch.setattr(
        "photo_worker.preview._to_srgb",
        lambda *_args: (_ for _ in ()).throw(ValueError("hostile color profile")),
    )

    with pytest.raises(PreviewError, match="normalization_failed"):
        generate_preview(source, output, max_input_bytes=10_000, max_pixels=10_000, slot=slot())

    assert not output.exists()


def test_generate_preview_rejects_output_that_exceeds_slot_bounds(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "preview.jpg"
    jpeg(source, (100, 80))

    with pytest.raises(PreviewError, match="output_contract_violation"):
        generate_preview(
            source,
            output,
            max_input_bytes=10_000_000,
            max_pixels=10_000,
            slot=slot(max_bytes=1),
        )

    assert not output.exists()
