from __future__ import annotations

from io import BytesIO

from detector_benchmark.normalize import normalize_image
from PIL import Image, ImageCms


def test_normalization_downscales_long_edge_without_upscaling() -> None:
    """A wrong resize scale would change detector geometry and invalidate the comparison."""
    source = Image.new("RGB", (3200, 1600), "white")
    content = BytesIO()
    source.save(content, format="JPEG")

    normalized = normalize_image(content.getvalue())

    assert normalized.original_size == (3200, 1600)
    assert normalized.size == (1600, 800)
    assert normalized.resize_scale == 0.5


def test_normalization_keeps_smaller_inputs_at_their_original_geometry() -> None:
    """Upscaling a small selfie would make the normalized detector variant incomparable."""
    source = Image.new("RGB", (800, 400), "white")
    content = BytesIO()
    source.save(content, format="JPEG")

    normalized = normalize_image(content.getvalue())

    assert normalized.original_size == (800, 400)
    assert normalized.size == (800, 400)
    assert normalized.resize_scale == 1.0


def test_normalization_embeds_srgb_profile_like_the_gallery_preview() -> None:
    """Omitting the gallery's ICC conversion would change detector input pixels."""
    source = Image.new("RGB", (20, 10), "red")
    content = BytesIO()
    source.save(
        content,
        format="JPEG",
        icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
    )

    normalized = normalize_image(content.getvalue())

    with Image.open(BytesIO(normalized.content)) as result:
        assert result.format == "JPEG"
        assert result.info["icc_profile"]
