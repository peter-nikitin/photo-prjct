from __future__ import annotations

from io import BytesIO

from django import forms
from django.conf import settings
from PIL import Image, UnidentifiedImageError

_PIXEL_LIMIT_ERROR = "Изображение не должно превышать 25 000 000 пикселей."


class SelfieSearchUploadForm(forms.Form):
    selfie = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"})
    )

    def clean_selfie(self):
        upload = self.cleaned_data["selfie"]
        if upload.size <= 0:
            raise forms.ValidationError("Выберите непустой файл JPEG или PNG.")
        if upload.size > settings.SELFIE_SEARCH_MAX_UPLOAD_BYTES:
            raise forms.ValidationError("Размер селфи не должен превышать 20 МиБ.")

        content = upload.read()
        try:
            image = Image.open(BytesIO(content))
        except Image.DecompressionBombError:
            raise forms.ValidationError(_PIXEL_LIMIT_ERROR) from None
        except (UnidentifiedImageError, OSError):
            if upload.content_type in {"image/jpeg", "image/png"}:
                raise forms.ValidationError("Файл повреждён. Выберите другое селфи.") from None
            raise forms.ValidationError("Загрузите файл JPEG или PNG.") from None
        image_format = image.format
        if image_format is None:
            raise forms.ValidationError("Загрузите файл JPEG или PNG.")
        expected_content_type = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image_format)
        if expected_content_type is None or upload.content_type != expected_content_type:
            raise forms.ValidationError("Загрузите файл JPEG или PNG.")
        width, height = image.size
        if width * height > settings.SELFIE_SEARCH_MAX_PIXELS:
            raise forms.ValidationError(_PIXEL_LIMIT_ERROR)
        try:
            image.verify()
            verified = Image.open(BytesIO(content))
            verified.load()
        except Image.DecompressionBombError:
            raise forms.ValidationError(_PIXEL_LIMIT_ERROR) from None
        except (OSError, SyntaxError):
            raise forms.ValidationError("Файл повреждён. Выберите другое селфи.") from None
        upload.seek(0)
        return upload
