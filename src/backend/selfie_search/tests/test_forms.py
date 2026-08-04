from io import BytesIO
from pathlib import Path
from struct import pack
from unittest.mock import patch
from zlib import crc32

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image
from selfie_search.forms import SelfieSearchUploadForm, SelfieUploadObservation
from selfie_search.images import PreparedSelfie, SelfieImageRejected

FIXTURE = Path(__file__).parent / "fixtures" / "iphone-oriented.heic"


def image_upload(
    *,
    image_format: str,
    size: tuple[int, int] = (8, 8),
    content_type: str | None = None,
    filename: str | None = None,
) -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", size, color="white").save(content, format=image_format)
    extension = image_format.lower()
    declared_type = content_type or ("image/jpeg" if image_format == "JPEG" else "image/png")
    return SimpleUploadedFile(
        filename or f"selfie.{extension}", content.getvalue(), content_type=declared_type
    )


def heic_upload(*, content_type: str | None = "image/heic") -> SimpleUploadedFile:
    return SimpleUploadedFile("iphone.heic", FIXTURE.read_bytes(), content_type=content_type)


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
                    files={
                        "selfie": image_upload(
                            image_format=image_format,
                            content_type="application/octet-stream",
                            filename="selfie.bin",
                        )
                    }
                )

                self.assertTrue(form.is_valid(), form.errors)
                self.assertIsInstance(form.cleaned_data["selfie"], PreparedSelfie)

    def test_accepts_a_real_heic_with_a_generic_declared_type(self) -> None:
        form = SelfieSearchUploadForm(
            files={"selfie": heic_upload(content_type="application/octet-stream")}
        )

        self.assertTrue(form.is_valid(), form.errors)
        prepared = form.cleaned_data["selfie"]
        self.assertIsInstance(prepared, PreparedSelfie)
        self.assertEqual(prepared.content_type, "image/jpeg")

    def test_rejects_missing_selfie(self) -> None:
        form = SelfieSearchUploadForm(files={})

        self.assertFalse(form.is_valid())
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "missing_or_empty")
        self.assertEqual(error.message, "Выберите фотографию для поиска.")

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
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "unsupported_format")
        self.assertEqual(
            error.message,
            "Не удалось прочитать фотографию. Выберите JPEG, PNG, HEIC или HEIF.",
        )

    def test_rejects_truncated_image(self) -> None:
        upload = image_upload(image_format="JPEG")
        truncated = SimpleUploadedFile("selfie.jpg", upload.read()[:12], content_type="image/jpeg")
        form = SelfieSearchUploadForm(files={"selfie": truncated})

        self.assertFalse(form.is_valid())
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "corrupt_image")
        self.assertEqual(error.message, "Фотография повреждена. Выберите другой файл.")

    def test_rejects_unsupported_actual_image_format(self) -> None:
        content = BytesIO()
        Image.new("RGB", (8, 8), color="white").save(content, format="GIF")
        upload = SimpleUploadedFile("selfie.png", content.getvalue(), content_type="image/png")
        form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "unsupported_format")
        self.assertEqual(
            error.message,
            "Не удалось прочитать фотографию. Выберите JPEG, PNG, HEIC или HEIF.",
        )

    def test_rejects_upload_larger_than_twenty_mebibytes(self) -> None:
        upload = SimpleUploadedFile(
            "selfie.jpg", b"x" * (20 * 1024 * 1024 + 1), content_type="image/jpeg"
        )
        form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "source_too_large")
        self.assertEqual(error.message, "Размер фотографии не должен превышать 20 МиБ.")

    def test_rejects_image_with_more_than_twenty_five_million_pixels(self) -> None:
        upload = image_upload(image_format="PNG", size=(5001, 5000))
        with patch("PIL.ImageFile.ImageFile.load", autospec=True) as load:
            form = SelfieSearchUploadForm(files={"selfie": upload})

        self.assertFalse(form.is_valid())
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "pixel_limit_exceeded")
        self.assertEqual(
            error.message,
            "Изображение слишком большое. Уменьшите его так, чтобы ширина × высота были не больше "
            "25 млн пикселей — например, 5000 × 5000.",
        )
        load.assert_not_called()

    def test_rejects_a_small_png_that_raises_a_decompression_bomb_at_open(self) -> None:
        form = SelfieSearchUploadForm(
            files={"selfie": png_ihdr_upload(width=100_000, height=100_000)}
        )

        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors.as_data()["selfie"][0].code,
            "pixel_limit_exceeded",
        )

    def test_rejects_a_decompression_bomb_raised_while_decoding(self) -> None:
        upload = image_upload(image_format="PNG")
        verified = Image.open(BytesIO(upload.read()))
        upload.seek(0)
        with patch(
            "selfie_search.images.Image.open",
            side_effect=(verified, Image.DecompressionBombError("declared pixels are unsafe")),
        ):
            form = SelfieSearchUploadForm(files={"selfie": upload})
            self.assertFalse(form.is_valid())

        self.assertEqual(
            form.errors.as_data()["selfie"][0].code,
            "pixel_limit_exceeded",
        )

    def test_exposes_the_normalized_size_rejection_code_and_message(self) -> None:
        with patch(
            "selfie_search.forms.prepare_selfie_image",
            side_effect=SelfieImageRejected("normalized_too_large", "heic"),
        ) as prepare:
            form = SelfieSearchUploadForm(files={"selfie": heic_upload()})
            self.assertFalse(form.is_valid())

        prepare.assert_called_once()
        error = form.errors.as_data()["selfie"][0]
        self.assertEqual(error.code, "normalized_too_large")
        self.assertEqual(error.message, "Размер фотографии не должен превышать 20 МиБ.")
