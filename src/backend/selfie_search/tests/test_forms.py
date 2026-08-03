from io import BytesIO
from struct import pack
from unittest.mock import patch
from zlib import crc32

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image
from selfie_search.forms import SelfieSearchUploadForm, SelfieUploadObservation


def image_upload(*, image_format: str, size: tuple[int, int] = (8, 8)) -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", size, color="white").save(content, format=image_format)
    extension = image_format.lower()
    content_type = "image/jpeg" if image_format == "JPEG" else "image/png"
    return SimpleUploadedFile(f"selfie.{extension}", content.getvalue(), content_type=content_type)


def png_ihdr_upload(*, width: int, height: int) -> SimpleUploadedFile:
    """Make a tiny PNG whose IHDR declares a dangerous pixel count."""
    ihdr = pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    content = b"\x89PNG\r\n\x1a\n" + pack(">I", len(ihdr)) + b"IHDR" + ihdr
    content += pack(">I", crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    content += pack(">I", 0) + b"IEND" + pack(">I", crc32(b"IEND") & 0xFFFFFFFF)
    return SimpleUploadedFile("selfie.png", content, content_type="image/png")


class SelfieSearchUploadFormTests(SimpleTestCase):
    """The production breaks caught here are accepting unsafe or non-image selfie inputs."""

    def test_accepts_a_real_jpeg_and_png(self) -> None:
        for image_format in ("JPEG", "PNG"):
            with self.subTest(image_format=image_format):
                form = SelfieSearchUploadForm(
                    files={"selfie": image_upload(image_format=image_format)}
                )

                self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_missing_selfie(self) -> None:
        form = SelfieSearchUploadForm(files={})

        self.assertFalse(form.is_valid())
        self.assertIn("selfie", form.errors)

    def test_upload_with_clear_checkbox_remains_accepted(self) -> None:
        form = SelfieSearchUploadForm(
            data={"selfie-clear": "on"}, files={"selfie": image_upload(image_format="JPEG")}
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_clear_only_is_a_bounded_missing_or_empty_rejection(self) -> None:
        form = SelfieSearchUploadForm(data={"selfie-clear": "on"}, files={})

        self.assertFalse(form.is_valid())
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.message, "This field is required.")
        self.assertEqual(error.code, "missing_or_empty")

    def test_validation_errors_keep_existing_messages_and_expose_bounded_reason_codes(self) -> None:
        cases = (
            (
                "missing",
                SelfieSearchUploadForm(files={}),
                "This field is required.",
                "missing_or_empty",
            ),
            (
                "empty",
                SelfieSearchUploadForm(
                    files={
                        "selfie": SimpleUploadedFile("selfie.jpg", b"", content_type="image/jpeg")
                    }
                ),
                "The submitted file is empty.",
                "missing_or_empty",
            ),
            (
                "unsupported",
                SelfieSearchUploadForm(
                    files={
                        "selfie": SimpleUploadedFile(
                            "selfie.jpg", b"not an image", content_type="image/gif"
                        )
                    }
                ),
                "Загрузите файл JPEG или PNG.",
                "unsupported_format",
            ),
            (
                "corrupt",
                SelfieSearchUploadForm(
                    files={
                        "selfie": SimpleUploadedFile(
                            "selfie.jpg", b"not an image", content_type="image/jpeg"
                        )
                    }
                ),
                "Файл повреждён. Выберите другое селфи.",
                "corrupt_image",
            ),
            (
                "too_large",
                SelfieSearchUploadForm(
                    files={
                        "selfie": SimpleUploadedFile(
                            "selfie.jpg",
                            b"x" * (20 * 1024 * 1024 + 1),
                            content_type="image/jpeg",
                        )
                    }
                ),
                "Размер селфи не должен превышать 20 МиБ.",
                "source_too_large",
            ),
            (
                "pixel_limit",
                SelfieSearchUploadForm(
                    files={"selfie": image_upload(image_format="PNG", size=(5001, 5000))}
                ),
                "Изображение не должно превышать 25 000 000 пикселей.",
                "pixel_limit_exceeded",
            ),
        )
        for name, form, message, code in cases:
            with self.subTest(name=name):
                self.assertFalse(form.is_valid())
                error = form.errors.as_data()["selfie"][0]
                self.assertEqual(error.message, message)
                self.assertEqual(error.code, code)

    def test_observation_is_frozen_after_validation_without_rereading_upload(self) -> None:
        upload = image_upload(image_format="JPEG")
        form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertTrue(form.is_valid(), form.errors)
        upload.file = type(
            "UnreadableUpload", (), {"read": lambda _self: (_ for _ in ()).throw(AssertionError())}
        )()
        with patch.object(
            upload.file, "read", side_effect=AssertionError("upload read after validation")
        ):
            observation = form.observation()

        self.assertEqual(
            observation,
            SelfieUploadObservation(
                actual_format="jpeg", declared_type="jpeg", source_size_bucket="le_1mib"
            ),
        )

    def test_observation_uses_unknown_actual_format_when_pillow_cannot_identify_upload(
        self,
    ) -> None:
        form = SelfieSearchUploadForm(
            files={
                "selfie": SimpleUploadedFile(
                    "selfie.jpg", b"not an image", content_type="image/jpeg"
                )
            }
        )

        self.assertFalse(form.is_valid())

        self.assertEqual(
            form.observation(),
            SelfieUploadObservation(
                actual_format="unknown", declared_type="jpeg", source_size_bucket="le_1mib"
            ),
        )

    def test_rejects_spoofed_content_type_and_extension(self) -> None:
        upload = SimpleUploadedFile("selfie.jpg", b"not an image", content_type="image/gif")
        form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("JPEG или PNG", form.errors["selfie"][0])

    def test_rejects_truncated_image(self) -> None:
        upload = image_upload(image_format="JPEG")
        truncated = SimpleUploadedFile("selfie.jpg", upload.read()[:12], content_type="image/jpeg")
        form = SelfieSearchUploadForm(files={"selfie": truncated})

        self.assertFalse(form.is_valid())
        self.assertIn("повреждён", form.errors["selfie"][0])

    def test_rejects_unsupported_actual_image_format(self) -> None:
        content = BytesIO()
        Image.new("RGB", (8, 8), color="white").save(content, format="GIF")
        upload = SimpleUploadedFile("selfie.png", content.getvalue(), content_type="image/png")
        form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("JPEG или PNG", form.errors["selfie"][0])

    def test_rejects_upload_larger_than_twenty_mebibytes(self) -> None:
        upload = SimpleUploadedFile(
            "selfie.jpg", b"x" * (20 * 1024 * 1024 + 1), content_type="image/jpeg"
        )
        form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("20 МиБ", form.errors["selfie"][0])

    def test_rejects_image_with_more_than_twenty_five_million_pixels(self) -> None:
        upload = image_upload(image_format="PNG", size=(5001, 5000))
        with patch("PIL.ImageFile.ImageFile.load", autospec=True) as load:
            form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("25 000 000", form.errors["selfie"][0])
        load.assert_not_called()

    def test_rejects_a_small_png_that_raises_a_decompression_bomb_at_open(self) -> None:
        form = SelfieSearchUploadForm(
            files={"selfie": png_ihdr_upload(width=100_000, height=100_000)}
        )

        self.assertFalse(form.is_valid())
        self.assertIn("25 000 000", form.errors["selfie"][0])

    def test_rejects_a_decompression_bomb_raised_while_decoding(self) -> None:
        upload = image_upload(image_format="PNG")
        verified = Image.open(BytesIO(upload.read()))
        upload.seek(0)
        with patch(
            "selfie_search.forms.Image.open",
            side_effect=(verified, Image.DecompressionBombError("declared pixels are unsafe")),
        ):
            form = SelfieSearchUploadForm(files={"selfie": upload})
            self.assertFalse(form.is_valid())

        self.assertIn("25 000 000", form.errors["selfie"][0])
