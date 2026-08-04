from io import BytesIO
from struct import pack
from unittest.mock import patch
from zlib import crc32

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image
from selfie_search.forms import FeedbackSubmissionForm, SelfieSearchUploadForm


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


class FeedbackSubmissionFormTests(SimpleTestCase):
    """The production breaks caught here are accepting unsafe feedback metadata or media."""

    def make_form(self, *, data=None, upload=None) -> FeedbackSubmissionForm:
        return FeedbackSubmissionForm(
            data={
                "contact": "  person@example.test  ",
                "personal_data_consent": "on",
                "labels": '{"00000000-0000-0000-0000-000000000001":"present"}',
                **(data or {}),
            },
            files={"selfie": upload or image_upload(image_format="JPEG")},
        )

    def test_accepts_valid_multipart_payload_and_uses_server_consent_version(self) -> None:
        form = self.make_form()

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["contact"], "person@example.test")
        self.assertEqual(
            form.cleaned_data["labels"],
            {"00000000-0000-0000-0000-000000000001": "present"},
        )
        self.assertEqual(form.consent_text_version, "2026-08-04")

    def test_rejects_unconsented_or_unsafe_contact_and_duplicate_labels(self) -> None:
        cases = (
            ({"personal_data_consent": ""}, "personal_data_consent"),
            ({"contact": " \n "}, "contact"),
            ({"contact": "x\x00@example.test"}, "contact"),
            ({"contact": "x" * 255}, "contact"),
            (
                {
                    "labels": (
                        '{"00000000-0000-0000-0000-000000000001":"present",'
                        '"00000000-0000-0000-0000-000000000001":"absent"}'
                    )
                },
                "labels",
            ),
        )
        for data, field in cases:
            with self.subTest(data=data):
                form = self.make_form(data=data)

                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_rejects_unknown_label_value_and_corrupt_or_oversize_image(self) -> None:
        unknown_label = self.make_form(
            data={"labels": '{"00000000-0000-0000-0000-000000000001":"maybe"}'}
        )
        corrupt = self.make_form(
            upload=SimpleUploadedFile("selfie.jpg", b"broken", content_type="image/jpeg")
        )
        oversize = self.make_form(
            upload=SimpleUploadedFile(
                "selfie.jpg", b"x" * (20 * 1024 * 1024 + 1), content_type="image/jpeg"
            )
        )

        for form, field in ((unknown_label, "labels"), (corrupt, "selfie"), (oversize, "selfie")):
            with self.subTest(field=field):
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
