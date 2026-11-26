from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings

from ingestion.models import MAX_POSTGRES_BIGINT


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
    folder_id = forms.IntegerField(required=False, min_value=1)
    last_modified_ms = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=MAX_POSTGRES_BIGINT,
    )
    ambiguous_sha256 = forms.RegexField(
        regex=r"^[0-9a-f]{64}$",
        required=False,
        empty_value=None,
    )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if (
            cleaned_data.get("ambiguous_sha256") is not None
            and cleaned_data.get("last_modified_ms") is None
        ):
            self.add_error(
                "ambiguous_sha256",
                "A SHA-256 fingerprint requires a last-modified timestamp.",
            )
        return cleaned_data


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


class DiagnosticIntegerField(forms.IntegerField):
    def to_python(self, value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise forms.ValidationError("Submit an integer.")
        return super().to_python(value)


class FailureForm(forms.Form):
    code = forms.ChoiceField(
        choices=tuple(
            (code, code)
            for code in (
                "transfer_http_error",
                "transfer_network_error",
                "transfer_timeout",
                "transfer_cancelled",
            )
        )
    )
    http_status = DiagnosticIntegerField(min_value=0, max_value=599)
    post_attempts = DiagnosticIntegerField(min_value=0, max_value=5)
    post_duration_ms = DiagnosticIntegerField(min_value=0, max_value=600000)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if set(self.data) - set(self.fields):
            raise forms.ValidationError("Unexpected diagnostic fields.")
        status = cleaned_data.get("http_status")
        code = cleaned_data.get("code")
        if status is not None and (
            (code == "transfer_http_error" and status < 100)
            or (code != "transfer_http_error" and status != 0)
        ):
            self.add_error("http_status", "The status does not match the failure type.")
        if code != "transfer_cancelled" and cleaned_data.get("post_attempts") == 0:
            self.add_error("post_attempts", "A transfer failure requires an actual POST.")
        return cleaned_data


class EmptyJsonForm(forms.Form):
    pass


class ExactEmptyJsonForm(forms.Form):
    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if self.data:
            raise forms.ValidationError("Request body must be an empty object.")
        return cleaned_data
