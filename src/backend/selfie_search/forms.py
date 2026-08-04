from __future__ import annotations

import json
from io import BytesIO
from uuid import UUID

from django import forms
from django.conf import settings
from PIL import Image, UnidentifiedImageError

from selfie_search.models import FEEDBACK_CONSENT_TEXT_VERSION, JSON_MAX_BYTES

_PIXEL_LIMIT_ERROR = "Изображение не должно превышать 25 000 000 пикселей."


def validate_selfie_upload(upload):
    """Validate the exact JPEG/PNG contract shared by search and feedback uploads."""
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


class SelfieSearchUploadForm(forms.Form):
    selfie = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"})
    )

    def clean_selfie(self):
        return validate_selfie_upload(self.cleaned_data["selfie"])


class FeedbackSubmissionForm(forms.Form):
    selfie = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"})
    )
    contact = forms.CharField(max_length=254)
    personal_data_consent = forms.BooleanField(required=True)
    labels = forms.CharField(required=False)

    consent_text_version = FEEDBACK_CONSENT_TEXT_VERSION

    def clean_selfie(self):
        return validate_selfie_upload(self.cleaned_data["selfie"])

    def clean_contact(self) -> str:
        contact = self.cleaned_data["contact"].strip()
        if not contact:
            raise forms.ValidationError("Укажите контакт для связи.")
        if any(ord(character) < 32 or ord(character) == 127 for character in contact):
            raise forms.ValidationError("Контакт не должен содержать управляющие символы.")
        return contact

    def clean_labels(self) -> dict[str, str]:
        raw_labels = self.cleaned_data["labels"]
        if len(raw_labels.encode()) > JSON_MAX_BYTES:
            raise forms.ValidationError("Слишком много отметок.")
        try:
            labels = json.loads(raw_labels or "{}", object_pairs_hook=_unique_object)
        except (TypeError, ValueError, _DuplicateJsonKey):
            raise forms.ValidationError("Отметки должны быть корректным JSON-объектом.") from None
        if not isinstance(labels, dict):
            raise forms.ValidationError("Отметки должны быть JSON-объектом.")
        for result_id, value in labels.items():
            if not isinstance(result_id, str):
                raise forms.ValidationError("Идентификатор результата недопустим.")
            try:
                UUID(result_id)
            except ValueError:
                raise forms.ValidationError("Идентификатор результата недопустим.") from None
            if not isinstance(value, str) or value not in {"present", "absent"}:
                raise forms.ValidationError("Значение отметки недопустимо.")
        return labels


class _DuplicateJsonKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise _DuplicateJsonKey
    return value
