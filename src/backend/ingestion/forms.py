from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings


class BatchCreateForm(forms.Form):
    event_id = forms.IntegerField(min_value=1)
    expected_item_count = forms.IntegerField(
        min_value=1,
        max_value=settings.PHOTO_UPLOAD_MAX_FILES,
    )


class ItemForm(forms.Form):
    client_item_id = forms.UUIDField()
    filename = forms.CharField(max_length=255)
    content_type = forms.ChoiceField(choices=(("image/jpeg", "image/jpeg"),))
    size = forms.IntegerField(
        min_value=1,
        max_value=settings.PHOTO_UPLOAD_MAX_FILE_BYTES,
    )


class ItemRegistrationForm(forms.Form):
    items = forms.Field()

    def clean_items(self) -> list[dict[str, Any]]:
        items = self.cleaned_data["items"]
        if (
            not isinstance(items, list)
            or not 1 <= len(items) <= settings.PHOTO_UPLOAD_REGISTRATION_CHUNK
        ):
            raise forms.ValidationError("Submit between 1 and 100 files.")

        cleaned_items: list[dict[str, Any]] = []
        seen: set[object] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise forms.ValidationError(f"Item {index + 1} must be an object.")
            form = ItemForm(item)
            if not form.is_valid():
                raise forms.ValidationError(f"Item {index + 1} has invalid metadata.")
            client_item_id = form.cleaned_data["client_item_id"]
            if client_item_id in seen:
                raise forms.ValidationError("File identifiers must be unique.")
            seen.add(client_item_id)
            cleaned_items.append(form.cleaned_data)
        return cleaned_items


class AuthorizationForm(forms.Form):
    reason = forms.ChoiceField(
        choices=(("data_attempt", "data_attempt"), ("grant_refresh", "grant_refresh"))
    )


class FailureForm(forms.Form):
    code = forms.ChoiceField(
        choices=(
            ("transfer_retries_exhausted", "transfer_retries_exhausted"),
            ("transfer_cancelled", "transfer_cancelled"),
        )
    )


class EmptyJsonForm(forms.Form):
    pass
