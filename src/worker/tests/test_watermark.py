from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from photo_worker.contracts import OutputSlot
from photo_worker.watermark import WatermarkedPreviewError, generate_watermarked_preview
from PIL import Image, ImageCms

ATTEMPT_ID = "00000000-0000-0000-0000-000000000012"


def slot(*, max_bytes: int = 10_485_760) -> OutputSlot:
    return OutputSlot(
        variant="preview-watermarked-v1",
        upload_url="https://storage.example.test/upload",
        upload_expires_at="2026-08-20T10:01:00Z",
        content_type="image/jpeg",
        staging_key=(f"processing-pending/previews/{ATTEMPT_ID}/preview-watermarked-v1.jpg"),
        max_bytes=max_bytes,
        max_width=1600,
        max_height=1600,
        checksum_algorithm="sha256",
    )


def write_clean(path: Path, size: tuple[int, int], *, color: str = "white") -> None:
    image = Image.new("RGB", size, color)
    image.save(path, "JPEG", exif=b"Exif\x00\x00test")
    image.close()


def write_overlay(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> str:
    image = Image.new("RGBA", size, color)
    image.save(path, "PNG")
    image.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def watermark_assets(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    assets = tmp_path / "assets"
    assets.mkdir()
    landscape = write_overlay(assets / "watermark-landscape-v1.png", (4, 2), (255, 0, 0, 128))
    portrait = write_overlay(assets / "watermark-portrait-v1.png", (2, 4), (0, 0, 255, 128))
    return assets, {"landscape": landscape, "portrait": portrait}


def pixel_is_close(actual: tuple[int, int, int], expected: tuple[int, int, int]) -> bool:
    return all(abs(got - want) <= 25 for got, want in zip(actual, expected, strict=True))


@pytest.mark.parametrize(
    ("size", "expected"),
    [((4, 2), (255, 127, 127)), ((4, 4), (255, 127, 127)), ((2, 4), (127, 127, 255))],
)
def test_renderer_selects_the_orientation_asset_and_preserves_clean_dimensions(
    tmp_path: Path, size: tuple[int, int], expected: tuple[int, int, int]
) -> None:
    """Using the wrong orientation asset would produce the wrong visible overlay color."""
    source = tmp_path / "clean.jpg"
    destination = tmp_path / "watermarked.jpg"
    assets, asset_sha256s = watermark_assets(tmp_path)
    write_clean(source, size)

    result = generate_watermarked_preview(
        source,
        destination,
        max_input_bytes=1_000_000,
        max_pixels=24_000_000,
        slot=slot(),
        asset_directory=assets,
        asset_sha256s=asset_sha256s,
    )

    with Image.open(destination) as output:
        assert output.size == size
        assert pixel_is_close(
            output.convert("RGB").getpixel((size[0] // 2, size[1] // 2)), expected
        )
    assert result.variant == "preview-watermarked-v1"
    assert result.width == size[0]
    assert result.height == size[1]


def test_renderer_applies_centered_cover_crop_and_alpha_without_copying_metadata(
    tmp_path: Path,
) -> None:
    """Removing centered crop, alpha composition, or metadata stripping changes output bytes."""
    source = tmp_path / "clean.jpg"
    destination = tmp_path / "watermarked.jpg"
    assets = tmp_path / "assets"
    assets.mkdir()
    landscape_path = assets / "watermark-landscape-v1.png"
    overlay = Image.new("RGBA", (4, 2))
    for x in range(4):
        for y in range(2):
            overlay.putpixel((x, y), (255, 0, 0, 128) if x < 2 else (0, 0, 255, 128))
    overlay.save(landscape_path, "PNG")
    overlay.close()
    portrait_hash = write_overlay(assets / "watermark-portrait-v1.png", (2, 4), (0, 255, 0, 128))
    asset_sha256s = {
        "landscape": hashlib.sha256(landscape_path.read_bytes()).hexdigest(),
        "portrait": portrait_hash,
    }
    write_clean(source, (4, 4))

    generate_watermarked_preview(
        source,
        destination,
        max_input_bytes=1_000_000,
        max_pixels=24_000_000,
        slot=slot(),
        asset_directory=assets,
        asset_sha256s=asset_sha256s,
    )

    with Image.open(destination) as output:
        assert pixel_is_close(output.convert("RGB").getpixel((0, 2)), (255, 127, 127))
        assert pixel_is_close(output.convert("RGB").getpixel((3, 2)), (127, 127, 255))
        assert output.getexif() == {}
        assert output.mode == "RGB"
        assert "icc_profile" in output.info
        assert "sRGB" in ImageCms.getProfileName(
            ImageCms.ImageCmsProfile(io.BytesIO(output.info["icc_profile"]))
        )


@pytest.mark.parametrize(
    ("source_bytes", "source_suffix", "expected_code"),
    [(b"not-an-image", ".jpg", "decode_failed"), (b"plain text", ".txt", "decode_failed")],
)
def test_renderer_rejects_corrupt_or_unsupported_clean_input_without_output(
    tmp_path: Path, source_bytes: bytes, source_suffix: str, expected_code: str
) -> None:
    """Accepting an invalid clean preview could publish non-image bytes as a watermark result."""
    source = tmp_path / f"clean{source_suffix}"
    destination = tmp_path / "watermarked.jpg"
    assets, asset_sha256s = watermark_assets(tmp_path)
    source.write_bytes(source_bytes)

    with pytest.raises(WatermarkedPreviewError, match=expected_code):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=24_000_000,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )

    assert not destination.exists()


def test_renderer_rejects_a_png_clean_input_without_output(tmp_path: Path) -> None:
    """Accepting a non-JPEG clean derivative would break the versioned input contract."""
    source = tmp_path / "clean.png"
    destination = tmp_path / "watermarked.jpg"
    assets, asset_sha256s = watermark_assets(tmp_path)
    png = Image.new("RGB", (4, 2), "white")
    png.save(source, "PNG")
    png.close()

    with pytest.raises(WatermarkedPreviewError, match="unsupported_input"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=24_000_000,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "overlay_bytes",
    [b"not-a-png", b""],
)
def test_renderer_rejects_corrupt_or_empty_overlay_without_output(
    tmp_path: Path, overlay_bytes: bytes
) -> None:
    """A broken packaged overlay must fail before the worker can upload derivative bytes."""
    source = tmp_path / "clean.jpg"
    destination = tmp_path / "watermarked.jpg"
    assets, asset_sha256s = watermark_assets(tmp_path)
    write_clean(source, (4, 2))
    selected = assets / "watermark-landscape-v1.png"
    selected.write_bytes(overlay_bytes)
    asset_sha256s = {
        **asset_sha256s,
        "landscape": hashlib.sha256(selected.read_bytes()).hexdigest(),
    }

    with pytest.raises(WatermarkedPreviewError, match="watermark_asset_invalid"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=24_000_000,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )

    assert not destination.exists()


def test_renderer_rejects_non_rgba_overlay_and_checksum_mismatch_without_output(
    tmp_path: Path,
) -> None:
    """Changing overlay mode or bytes must be detected before image composition."""
    source = tmp_path / "clean.jpg"
    destination = tmp_path / "watermarked.jpg"
    assets, asset_sha256s = watermark_assets(tmp_path)
    write_clean(source, (4, 2))
    selected = assets / "watermark-landscape-v1.png"
    rgb_overlay = Image.new("RGB", (4, 2), "red")
    rgb_overlay.save(selected, "PNG")
    rgb_overlay.close()

    with pytest.raises(WatermarkedPreviewError, match="watermark_asset_invalid"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=24_000_000,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s={
                **asset_sha256s,
                "landscape": hashlib.sha256(selected.read_bytes()).hexdigest(),
            },
        )
    assert not destination.exists()

    write_overlay(selected, (4, 2), (255, 0, 1, 128))
    with pytest.raises(WatermarkedPreviewError, match="watermark_asset_mismatch"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=24_000_000,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )
    assert not destination.exists()


def test_renderer_enforces_input_pixel_and_output_byte_bounds(tmp_path: Path) -> None:
    """Skipping a bound could exceed the worker memory or attempt-scoped output grant."""
    source = tmp_path / "clean.jpg"
    destination = tmp_path / "watermarked.jpg"
    assets, asset_sha256s = watermark_assets(tmp_path)
    write_clean(source, (4, 2))

    with pytest.raises(WatermarkedPreviewError, match="input_too_large"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1,
            max_pixels=24_000_000,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )
    with pytest.raises(WatermarkedPreviewError, match="input_too_large"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=7,
            slot=slot(),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )
    with pytest.raises(WatermarkedPreviewError, match="output_contract_violation"):
        generate_watermarked_preview(
            source,
            destination,
            max_input_bytes=1_000_000,
            max_pixels=24_000_000,
            slot=slot(max_bytes=1),
            asset_directory=assets,
            asset_sha256s=asset_sha256s,
        )
    assert not destination.exists()
