from __future__ import annotations

from django import forms

from selfie_search.images import SelfieImageRejected, prepare_selfie_image

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


class SelfieSearchUploadForm(forms.Form):
    selfie = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/heic,image/heif,.heic,.heif",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.image_rejection: SelfieImageRejected | None = None

    def clean_selfie(self):
        upload = self.cleaned_data.get("selfie")
        if upload is None:
            rejection = SelfieImageRejected("missing_or_empty", None)
            self.image_rejection = rejection
            raise forms.ValidationError(
                _REJECTION_MESSAGES[rejection.reason], code=rejection.reason
            )
        try:
            return prepare_selfie_image(upload)
        except SelfieImageRejected as rejected:
            self.image_rejection = rejected
            raise forms.ValidationError(
                _REJECTION_MESSAGES[rejected.reason], code=rejected.reason
            ) from None
