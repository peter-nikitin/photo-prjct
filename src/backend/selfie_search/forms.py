from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from django import forms
from django.conf import settings
from PIL import Image, UnidentifiedImageError

from selfie_search.observability import actual_format_label, declared_type_label, source_size_bucket

_PIXEL_LIMIT_ERROR = "Изображение не должно превышать 25 000 000 пикселей."


@dataclass(frozen=True)
class SelfieUploadObservation:
    actual_format: str
    declared_type: str
    source_size_bucket: str


class _SelfieFileField(forms.FileField):
    def to_python(self, data):
        try:
            return super().to_python(data)
        except forms.ValidationError as error:
            if error.code != "empty":
                raise
            raise forms.ValidationError(error.messages[0], code="missing_or_empty") from None

    def validate(self, value) -> None:
        try:
            super().validate(value)
        except forms.ValidationError as error:
            if error.code != "required":
                raise
            raise forms.ValidationError(error.messages[0], code="missing_or_empty") from None


class SelfieSearchUploadForm(forms.Form):
    selfie = _SelfieFileField(
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        upload = self.files.get("selfie")
        self._observation = SelfieUploadObservation(
            actual_format="unknown",
            declared_type=declared_type_label(getattr(upload, "content_type", None)),
            source_size_bucket=source_size_bucket(getattr(upload, "size", 0)),
        )

    def observation(self) -> SelfieUploadObservation:
        """Return the labels captured during validation without revisiting upload bytes."""

        return self._observation

    def clean_selfie(self):
        upload = self.cleaned_data["selfie"]
        if upload.size <= 0:
            raise forms.ValidationError(
                "Выберите непустой файл JPEG или PNG.", code="missing_or_empty"
            )
        if upload.size > settings.SELFIE_SEARCH_MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                "Размер селфи не должен превышать 20 МиБ.", code="source_too_large"
            )

        content = upload.read()
        try:
            image = Image.open(BytesIO(content))
        except Image.DecompressionBombError:
            raise forms.ValidationError(_PIXEL_LIMIT_ERROR, code="pixel_limit_exceeded") from None
        except (UnidentifiedImageError, OSError):
            if upload.content_type in {"image/jpeg", "image/png"}:
                raise forms.ValidationError(
                    "Файл повреждён. Выберите другое селфи.", code="corrupt_image"
                ) from None
            raise forms.ValidationError(
                "Загрузите файл JPEG или PNG.", code="unsupported_format"
            ) from None
        image_format = image.format
        self._observation = SelfieUploadObservation(
            actual_format=actual_format_label(image_format),
            declared_type=self._observation.declared_type,
            source_size_bucket=self._observation.source_size_bucket,
        )
        if image_format is None:
            raise forms.ValidationError("Загрузите файл JPEG или PNG.", code="unsupported_format")
        expected_content_type = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image_format)
        if expected_content_type is None or upload.content_type != expected_content_type:
            raise forms.ValidationError("Загрузите файл JPEG или PNG.", code="unsupported_format")
        width, height = image.size
        if width * height > settings.SELFIE_SEARCH_MAX_PIXELS:
            raise forms.ValidationError(_PIXEL_LIMIT_ERROR, code="pixel_limit_exceeded")
        try:
            image.verify()
            verified = Image.open(BytesIO(content))
            verified.load()
        except Image.DecompressionBombError:
            raise forms.ValidationError(_PIXEL_LIMIT_ERROR, code="pixel_limit_exceeded") from None
        except (OSError, SyntaxError):
            raise forms.ValidationError(
                "Файл повреждён. Выберите другое селфи.", code="corrupt_image"
            ) from None
        upload.seek(0)
        return upload
