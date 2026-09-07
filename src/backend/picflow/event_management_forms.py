"""Strict administrative forms for event photo filtering and folder changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower
from processing.photo_status import (
    CATEGORY_CANCELLED,
    CATEGORY_FAILED,
    CATEGORY_NOT_REQUIRED,
    CATEGORY_NOT_STARTED,
    CATEGORY_PROCESSING,
    CATEGORY_QUEUED,
    CATEGORY_SUCCEEDED,
)

from picflow.forms import EventGalleryTimeFilterForm
from picflow.models import Event, EventFolder, Photo

PROCESSING_CATEGORY_CHOICES = (
    (CATEGORY_PROCESSING, "Обрабатывается"),
    (CATEGORY_QUEUED, "Ожидает обработки"),
    (CATEGORY_FAILED, "Ошибка"),
    (CATEGORY_CANCELLED, "Остановлена"),
    (CATEGORY_SUCCEEDED, "Обработано"),
    (CATEGORY_NOT_STARTED, "Не запущена"),
    (CATEGORY_NOT_REQUIRED, "Не требуется"),
)
PROCESSING_CATEGORIES = frozenset(value for value, _label in PROCESSING_CATEGORY_CHOICES)
VISIBILITY_ALL = "all"
VISIBILITY_VISIBLE = "visible"
VISIBILITY_HIDDEN = "hidden"
VISIBILITY_CHOICES = (
    (VISIBILITY_ALL, "Все"),
    (VISIBILITY_VISIBLE, "Видимые"),
    (VISIBILITY_HIDDEN, "Скрытые"),
)


@dataclass(frozen=True)
class EventPhotoFilters:
    """Validated event-local filter values reusable by reads and mutations."""

    folder_ids: tuple[int, ...] = ()
    include_unfiled: bool = False
    uploader_ids: tuple[int, ...] = ()
    include_unknown_uploader: bool = False
    capture_time_bounds: tuple[datetime | None, datetime | None] | None = None
    without_capture_time: bool = False
    visibility: str = VISIBILITY_ALL
    processing_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.visibility not in {VISIBILITY_ALL, VISIBILITY_VISIBLE, VISIBILITY_HIDDEN}:
            raise ValueError("Unsupported visibility filter.")
        if any(value not in PROCESSING_CATEGORIES for value in self.processing_categories):
            raise ValueError("Unsupported processing filter.")
        if self.without_capture_time and self.capture_time_bounds is not None:
            raise ValueError("Missing capture time cannot be combined with a time range.")


class EventPhotoFilterForm(forms.Form):
    """Validate all administrative photo filters without permissive fallbacks."""

    folder = forms.TypedMultipleChoiceField(
        required=False, coerce=int, widget=forms.CheckboxSelectMultiple
    )
    unfiled = forms.TypedChoiceField(
        required=False,
        choices=(("1", "Без папки"),),
        coerce=lambda value: value == "1",
        empty_value=False,
    )
    uploader = forms.TypedMultipleChoiceField(
        required=False, coerce=int, widget=forms.CheckboxSelectMultiple
    )
    uploader_unknown = forms.TypedChoiceField(
        required=False,
        choices=(("1", "Не указан"),),
        coerce=lambda value: value == "1",
        empty_value=False,
    )
    from_ = forms.CharField(required=False)
    to = forms.CharField(required=False)
    without_capture_time = forms.TypedChoiceField(
        required=False,
        choices=(("1", "Без времени съёмки"),),
        coerce=lambda value: value == "1",
        empty_value=False,
    )
    visibility = forms.ChoiceField(required=False, choices=VISIBILITY_CHOICES)
    processing = forms.MultipleChoiceField(
        required=False,
        choices=PROCESSING_CATEGORY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        self.event = event
        super().__init__(data=data, **kwargs)
        self._time_form = EventGalleryTimeFilterForm(event, data=data)
        self.fields["from"] = self._time_form.fields["from"]
        self.fields["to"] = self._time_form.fields["to"]
        self.fields["to"].widget.attrs.update(self.fields["from"].widget.attrs)
        self.fields.pop("from_", None)
        self.fields["folder"].choices = [(folder.pk, folder.name) for folder in event.folders.all()]
        user_model = get_user_model()
        username_field = user_model.USERNAME_FIELD
        uploaders = (
            user_model.objects.filter(uploaded_photos__event=event)
            .distinct()
            .order_by(Lower(username_field), "pk")
        )
        self.fields["uploader"].choices = [(user.pk, user.get_username()) for user in uploaders]
        if not Photo.objects.filter(event=event, uploaded_by__isnull=True).exists():
            self.fields["uploader_unknown"].choices = ()

    @property
    def filters(self) -> EventPhotoFilters:
        if not self.is_bound or not self.is_valid():
            raise ValueError("Filters are available only from a valid bound form.")
        return self.cleaned_data["filters"]

    @property
    def canonical_query(self) -> str:
        """Serialize only normalized, validated filter values."""
        filters = self.filters
        values: list[tuple[str, str | int]] = []
        values.extend(("folder", folder_id) for folder_id in filters.folder_ids)
        if filters.include_unfiled:
            values.append(("unfiled", "1"))
        values.extend(("uploader", uploader_id) for uploader_id in filters.uploader_ids)
        if filters.include_unknown_uploader:
            values.append(("uploader_unknown", "1"))
        for name in ("from", "to"):
            value = self.cleaned_data.get(name)
            if value:
                values.append((name, value))
        if filters.without_capture_time:
            values.append(("without_capture_time", "1"))
        if filters.visibility != VISIBILITY_ALL:
            values.append(("visibility", filters.visibility))
        values.extend(("processing", category) for category in filters.processing_categories)
        return urlencode(values)

    def clean(self):
        cleaned_data = super().clean()
        for field_name in (
            "unfiled",
            "uploader_unknown",
            "without_capture_time",
            "visibility",
        ):
            if hasattr(self.data, "getlist") and len(self.data.getlist(field_name)) > 1:
                self.add_error(field_name, "Укажите значение только один раз.")

        if self._time_form.is_requested and not self.event.timezone_name:
            for field_name in ("from", "to"):
                values = (
                    self.data.getlist(field_name)
                    if hasattr(self.data, "getlist")
                    else (self.data.get(field_name),)
                )
                if any(values):
                    self.add_error(
                        field_name,
                        "Для фильтра по времени у мероприятия должен быть указан часовой пояс.",
                    )
        elif not self._time_form.is_valid():
            for field_name, errors in self._time_form.errors.as_data().items():
                target = None if field_name == forms.forms.NON_FIELD_ERRORS else field_name
                for error in errors:
                    self.add_error(target, error)

        without_capture_time = bool(cleaned_data.get("without_capture_time"))
        if without_capture_time and self._time_form.is_requested:
            self.add_error(
                "without_capture_time",
                "Фильтр без времени съёмки нельзя объединить с диапазоном времени.",
            )
        if self.errors:
            return cleaned_data

        bounds = None if without_capture_time else self._time_form.utc_bounds
        cleaned_data["filters"] = EventPhotoFilters(
            folder_ids=tuple(sorted(set(cleaned_data.get("folder", ())))),
            include_unfiled=bool(cleaned_data.get("unfiled")),
            uploader_ids=tuple(sorted(set(cleaned_data.get("uploader", ())))),
            include_unknown_uploader=bool(cleaned_data.get("uploader_unknown")),
            capture_time_bounds=bounds,
            without_capture_time=without_capture_time,
            visibility=cleaned_data.get("visibility") or VISIBILITY_ALL,
            processing_categories=tuple(sorted(set(cleaned_data.get("processing", ())))),
        )
        return cleaned_data


class EventPhotoPageForm(forms.Form):
    """Reject ambiguous or malformed page input before pagination."""

    page = forms.IntegerField(required=False, min_value=1)

    def clean_page(self) -> int:
        values = self.data.getlist("page") if hasattr(self.data, "getlist") else ()
        if len(values) > 1:
            raise forms.ValidationError("Укажите страницу только один раз.")
        return self.cleaned_data.get("page") or 1


class EventPhotoActionForm(forms.Form):
    """Build exactly one validated explicit or live filtered selection."""

    selection_mode = forms.ChoiceField(
        choices=(("explicit", "Выбранные фотографии"), ("all_filtered", "Все результаты"))
    )
    action = forms.ChoiceField(
        choices=(("move", "Переместить"), ("hide", "Скрыть"), ("show", "Показать"))
    )
    target_folder = forms.ModelChoiceField(queryset=EventFolder.objects.none(), required=False)

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        self.event = event
        self._filter_form = EventPhotoFilterForm(event, data=data)
        super().__init__(data=data, **kwargs)
        self.fields["target_folder"].queryset = EventFolder.objects.filter(event=event)

    @property
    def selection(self):
        if not self.is_valid():
            raise ValueError("Selection is available only from a valid form.")
        return self.cleaned_data["selection"]

    def clean(self):
        cleaned_data = super().clean()
        for name in ("selection_mode", "action", "target_folder"):
            if hasattr(self.data, "getlist") and len(self.data.getlist(name)) > 1:
                self.add_error(name, "Укажите значение только один раз.")
        mode = cleaned_data.get("selection_mode")
        action = cleaned_data.get("action")
        target = cleaned_data.get("target_folder")
        photo_ids = tuple(dict.fromkeys(value for value in self.data.getlist("photo_id") if value))
        if action != "move" and target is not None:
            self.add_error("target_folder", "Папка используется только для перемещения.")
        if mode == "explicit":
            if not photo_ids:
                self.add_error("selection_mode", "Выберите хотя бы одну фотографию.")
            if self.errors:
                return cleaned_data
            from picflow.event_management import EventPhotoSelection

            cleaned_data["selection"] = EventPhotoSelection.explicit(photo_ids)
            return cleaned_data
        if mode == "all_filtered":
            if photo_ids:
                self.add_error(
                    "selection_mode", "Не передавайте отдельные ID для всех результатов."
                )
            if not self._filter_form.is_valid():
                self.add_error("selection_mode", "Исправьте фильтры перед массовым действием.")
            if self.errors:
                return cleaned_data
            from picflow.event_management import EventPhotoSelection

            cleaned_data["selection"] = EventPhotoSelection.all_filtered(self._filter_form.filters)
        return cleaned_data


class EventFolderNameForm(forms.Form):
    """Validate an event-folder name before a create or rename service call."""

    name = forms.CharField(max_length=255)

    def clean_name(self) -> str:
        return _clean_folder_name(self.cleaned_data["name"])


def _clean_folder_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise forms.ValidationError("Название папки не может быть пустым.")
    return name


class EventFolderTargetForm(forms.Form):
    """Resolve a folder only from the current event."""

    folder = forms.ModelChoiceField(queryset=EventFolder.objects.none())

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        super().__init__(data=data, **kwargs)
        self.fields["folder"].queryset = EventFolder.objects.filter(event=event)


def _add_service_error(form: forms.Form, error: ValidationError, *, default_field: str) -> None:
    if hasattr(error, "error_dict"):
        for field_name, errors in error.error_dict.items():
            target = field_name if field_name in form.fields else default_field
            for item in errors:
                form.add_error(target, item)
        return
    for item in error.error_list:
        form.add_error(default_field, item)


class EventFolderCreateForm(EventFolderNameForm):
    """Create a folder and keep expected database conflicts on the form."""

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        self.event = event
        super().__init__(data=data, **kwargs)

    def save(self) -> EventFolder | None:
        if not self.is_valid():
            return None
        from picflow.event_management import create_event_folder

        try:
            return create_event_folder(self.event, self.cleaned_data["name"])
        except ValidationError as error:
            _add_service_error(self, error, default_field="name")
            return None


class EventFolderRenameForm(forms.Form):
    """Rename an event-local folder and report name conflicts in place."""

    folder = forms.ModelChoiceField(queryset=EventFolder.objects.none())
    name = forms.CharField(max_length=255)

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        self.event = event
        super().__init__(data=data, **kwargs)
        self.fields["folder"].queryset = EventFolder.objects.filter(event=event)

    def clean_name(self) -> str:
        return _clean_folder_name(self.cleaned_data["name"])

    def save(self) -> EventFolder | None:
        if not self.is_valid():
            return None
        from picflow.event_management import rename_event_folder

        try:
            return rename_event_folder(
                self.event,
                self.cleaned_data["folder"],
                self.cleaned_data["name"],
            )
        except ValidationError as error:
            _add_service_error(self, error, default_field="name")
            return None


class EventFolderDeleteForm(EventFolderTargetForm):
    """Delete an empty event-local folder and surface protected references."""

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        self.event = event
        super().__init__(event, data=data, **kwargs)

    def delete(self) -> bool:
        if not self.is_valid():
            return False
        from picflow.event_management import delete_event_folder

        try:
            delete_event_folder(self.event, self.cleaned_data["folder"])
        except ValidationError as error:
            _add_service_error(self, error, default_field="folder")
            return False
        return True
