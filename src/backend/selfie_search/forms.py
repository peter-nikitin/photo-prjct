from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from django import forms

from selfie_search.images import PreparedSelfie, SelfieImageRejected, prepare_selfie_image
from selfie_search.models import FEEDBACK_CONSENT_TEXT_VERSION, JSON_MAX_BYTES
from selfie_search.observability import actual_format_label, declared_type_label, source_size_bucket

_REJECTION_MESSAGES = {
    "missing_or_empty": "Выберите фотографию для поиска.",
    "unsupported_format": "Не удалось прочитать фотографию. Выберите JPEG, PNG, HEIC или HEIF.",
    "corrupt_image": "Фотография повреждена. Выберите другой файл.",
    "source_too_large": "Размер фотографии не должен превышать 20 МиБ.",
    "normalized_too_large": "Размер фотографии не должен превышать 20 МиБ.",
    "pixel_limit_exceeded": (
        "Изображение слишком большое. Уменьшите его так, чтобы ширина × высота были не больше "
        "25 млн пикселей — например, 5000 × 5000."
    ),
}


@dataclass(frozen=True)
class SelfieUploadObservation:
    actual_format: str
    declared_type: str
    source_size_bucket: str


@dataclass(frozen=True)
class ValidatedSelfieUpload(PreparedSelfie):
    """Canonical bytes which also satisfy the legacy feedback-storage file interface."""

    def read(self) -> bytes:
        return self.content

    def seek(self, _position: int) -> int:
        return 0


def validate_selfie_upload(upload) -> ValidatedSelfieUpload:
    """Decode one allowed source once and expose canonical bytes to both submission paths."""
    if isinstance(upload, ValidatedSelfieUpload):
        return upload
    prepared = prepare_selfie_image(upload)
    return ValidatedSelfieUpload(
        content=prepared.content,
        content_type=prepared.content_type,
        source_size=prepared.source_size,
        source_format=prepared.source_format,
    )


class SelfieSearchUploadForm(forms.Form):
    selfie = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/heic,image/heif,.heic,.heif"}
        ),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        upload = self.files.get("selfie")
        self.image_rejection: SelfieImageRejected | None = None
        self._observation = SelfieUploadObservation(
            actual_format="unknown",
            declared_type=declared_type_label(getattr(upload, "content_type", None)),
            source_size_bucket=source_size_bucket(getattr(upload, "size", 0)),
        )

    def observation(self) -> SelfieUploadObservation:
        """Return bounded labels captured during validation without rereading upload bytes."""

        return self._observation

    def clean_selfie(self):
        upload = self.cleaned_data.get("selfie")
        if upload is None:
            rejection = SelfieImageRejected("missing_or_empty", None)
            self.image_rejection = rejection
            raise forms.ValidationError(
                _REJECTION_MESSAGES[rejection.reason], code=rejection.reason
            )
        try:
            prepared = validate_selfie_upload(upload)
        except SelfieImageRejected as rejected:
            self.image_rejection = rejected
            self._observation = SelfieUploadObservation(
                actual_format=actual_format_label(rejected.actual_format),
                declared_type=self._observation.declared_type,
                source_size_bucket=self._observation.source_size_bucket,
            )
            raise forms.ValidationError(
                _REJECTION_MESSAGES[rejected.reason], code=rejected.reason
            ) from None
        self._observation = SelfieUploadObservation(
            actual_format=actual_format_label(prepared.source_format),
            declared_type=self._observation.declared_type,
            source_size_bucket=self._observation.source_size_bucket,
        )
        return prepared


class FeedbackSubmissionForm(forms.Form):
    selfie = forms.FileField(
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/heic,image/heif,.heic,.heif"}
        )
    )
    contact = forms.CharField(max_length=254)
    personal_data_consent = forms.BooleanField(required=True)
    labels = forms.CharField(required=False)

    consent_text_version = FEEDBACK_CONSENT_TEXT_VERSION

    def clean_selfie(self) -> ValidatedSelfieUpload:
        try:
            return validate_selfie_upload(self.cleaned_data["selfie"])
        except SelfieImageRejected as rejected:
            raise forms.ValidationError(
                _REJECTION_MESSAGES[rejected.reason], code=rejected.reason
            ) from None

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
