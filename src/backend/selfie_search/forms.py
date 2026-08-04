from __future__ import annotations

from dataclasses import dataclass

from django import forms

from selfie_search.images import SelfieImageRejected, prepare_selfie_image
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
            prepared = prepare_selfie_image(upload)
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
