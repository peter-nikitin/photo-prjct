from __future__ import annotations

from io import BytesIO
from pathlib import Path
from struct import pack
from unittest.mock import Mock, patch
from zlib import crc32

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image
from selfie_search.images import (
    PreparedSelfie,
    SelfieImageRejected,
    prepare_selfie_image,
)

FIXTURE = Path(__file__).parent / "fixtures" / "iphone-oriented.heic"


def jpeg_upload(*, content_type: str = "image/jpeg") -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(content, format="JPEG")
    return SimpleUploadedFile("selfie.jpg", content.getvalue(), content_type=content_type)


def png_upload(*, content_type: str | None = "image/png") -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(content, format="PNG")
    return SimpleUploadedFile("selfie.png", content.getvalue(), content_type=content_type)


def png_ihdr_upload(*, width: int, height: int) -> SimpleUploadedFile:
    ihdr = pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    content = b"\x89PNG\r\n\x1a\n" + pack(">I", len(ihdr)) + b"IHDR" + ihdr
    content += pack(">I", crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    content += pack(">I", 0) + b"IEND" + pack(">I", crc32(b"IEND") & 0xFFFFFFFF)
    return SimpleUploadedFile("selfie.png", content, content_type="image/png")


class PrepareSelfieImageTests(SimpleTestCase):
    def test_normalizes_the_oriented_heic_fixture_to_a_metadata_free_quality_90_jpeg(self) -> None:
        content = FIXTURE.read_bytes()

        prepared = prepare_selfie_image(
            SimpleUploadedFile("iphone.heic", content, content_type="image/heic")
        )

        self.assertIsInstance(prepared, PreparedSelfie)
        self.assertEqual(prepared.source_size, len(content))
        self.assertEqual(prepared.source_format, "heic")
        self.assertEqual(prepared.content_type, "image/jpeg")
        with Image.open(BytesIO(prepared.content)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (4, 3))
            self.assertNotIn("exif", image.info)
            self.assertEqual(image.getexif(), {})
            self.assertEqual(
                image.quantization[0][:8],
                [3, 2, 2, 3, 5, 8, 10, 12],
            )
            top_left = image.getpixel((0, 0))
            top_right = image.getpixel((3, 0))
            bottom_left = image.getpixel((0, 2))
            bottom_right = image.getpixel((3, 2))
            self.assertGreater(top_left[2], 180)
            self.assertGreater(top_right[0], 180)
            self.assertGreater(bottom_left[0], 180)
            self.assertGreater(bottom_left[1], 180)
            self.assertGreater(bottom_right[1], 180)

    def test_preserves_verified_jpeg_bytes_and_canonical_content_type(self) -> None:
        upload = jpeg_upload(content_type="application/octet-stream")
        original = upload.read()
        upload.seek(0)

        prepared = prepare_selfie_image(upload)

        self.assertEqual(prepared.content, original)
        self.assertEqual(prepared.content_type, "image/jpeg")
        self.assertEqual(prepared.source_format, "jpeg")

    def test_preserves_verified_png_bytes_and_canonical_content_type(self) -> None:
        upload = png_upload(content_type=None)
        original = upload.read()
        upload.seek(0)

        prepared = prepare_selfie_image(upload)

        self.assertEqual(prepared.content, original)
        self.assertEqual(prepared.content_type, "image/png")
        self.assertEqual(prepared.source_format, "png")

    def test_rejects_a_normalized_object_over_the_bound(self) -> None:
        content = FIXTURE.read_bytes()

        with override_settings(SELFIE_SEARCH_MAX_UPLOAD_BYTES=len(content)):
            with patch(
                "selfie_search.images._normalize_heif",
                return_value=b"x" * (len(content) + 1),
            ):
                with self.assertRaises(SelfieImageRejected) as caught:
                    prepare_selfie_image(
                        SimpleUploadedFile("iphone.heic", content, content_type="image/heic")
                    )

        self.assertEqual(caught.exception.reason, "normalized_too_large")
        self.assertEqual(caught.exception.actual_format, "heic")

    def test_rejects_unsupported_actual_format_even_when_declared_as_jpeg(self) -> None:
        content = BytesIO()
        Image.new("RGB", (8, 8), color="white").save(content, format="GIF")

        with self.assertRaises(SelfieImageRejected) as caught:
            prepare_selfie_image(
                SimpleUploadedFile("selfie.jpg", content.getvalue(), content_type="image/jpeg")
            )

        self.assertEqual(caught.exception.reason, "unsupported_format")
        self.assertEqual(caught.exception.actual_format, "gif")

    def test_rejects_a_corrupt_supported_container(self) -> None:
        with self.assertRaises(SelfieImageRejected) as caught:
            prepare_selfie_image(
                SimpleUploadedFile("selfie.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
            )

        self.assertEqual(caught.exception.reason, "corrupt_image")
        self.assertIsNone(caught.exception.actual_format)

    def test_rejects_an_empty_upload(self) -> None:
        with self.assertRaises(SelfieImageRejected) as caught:
            prepare_selfie_image(SimpleUploadedFile("empty.jpg", b"", content_type="image/jpeg"))

        self.assertEqual(caught.exception.reason, "missing_or_empty")
        self.assertIsNone(caught.exception.actual_format)

    def test_rejects_a_source_over_the_bound_before_reading_the_decoder(self) -> None:
        upload = SimpleUploadedFile(
            "selfie.jpg",
            b"x" * (20 * 1024 * 1024 + 1),
            content_type="image/jpeg",
        )

        upload = Mock(size=upload.size, content_type=upload.content_type)
        upload.read = Mock()
        with patch.object(upload, "read", wraps=upload.read) as read:
            with self.assertRaises(SelfieImageRejected) as caught:
                prepare_selfie_image(upload)

        self.assertEqual(caught.exception.reason, "source_too_large")
        read.assert_not_called()

    def test_rejects_excessive_pixels_before_full_raster_allocation(self) -> None:
        upload = png_ihdr_upload(width=5_001, height=5_000)

        with patch("PIL.ImageFile.ImageFile.load", autospec=True) as load:
            with self.assertRaises(SelfieImageRejected) as caught:
                prepare_selfie_image(upload)

        self.assertEqual(caught.exception.reason, "pixel_limit_exceeded")
        load.assert_not_called()

    def test_maps_a_decoder_decompression_bomb_to_pixel_rejection(self) -> None:
        upload = png_ihdr_upload(width=100_000, height=100_000)

        with self.assertRaises(SelfieImageRejected) as caught:
            prepare_selfie_image(upload)

        self.assertEqual(caught.exception.reason, "pixel_limit_exceeded")
