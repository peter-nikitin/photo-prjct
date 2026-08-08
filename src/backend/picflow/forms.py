from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms

from picflow.models import Event


class EventGalleryTimeFilterForm(forms.Form):
    """Validate manual gallery times as unambiguous wall times in one event timezone."""

    fields: dict[str, forms.Field]
    from_ = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(attrs={"type": "datetime-local"}),
    )
    to = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, event: Event, data=None, **kwargs) -> None:
        self.event = event
        self.is_requested = bool(data and ("from" in data or "to" in data))
        self._repeated_fields = {
            name
            for name in ("from", "to")
            if hasattr(data, "getlist") and len(data.getlist(name)) > 1
        }
        super().__init__(data=data, **kwargs)
        self.fields = {"from": self.fields["from_"], "to": self.fields["to"]}
        self.fields["from"].widget.attrs.update(
            min=datetime.combine(event.start_date, time.min).strftime("%Y-%m-%dT%H:%M"),
            max=datetime.combine(event.end_date, time(23, 59)).strftime("%Y-%m-%dT%H:%M"),
        )

    @property
    def utc_bounds(self) -> tuple[datetime, datetime] | None:
        if not self.is_bound or not self.is_valid() or not self.is_requested:
            return None
        return self.cleaned_data["utc_bounds"]

    def clean(self):
        cleaned_data = super().clean()
        if not self.is_requested:
            return cleaned_data
        for name in self._repeated_fields:
            self.add_error(name, "Укажите значение времени только один раз.")

        from_value = cleaned_data.get("from")
        to_value = cleaned_data.get("to")
        if not from_value:
            self.add_error("from", "Укажите время начала.")
        start = self._parse_event_time(from_value, "from") if from_value else None
        end = self._parse_event_time(to_value, "to") if to_value else None
        if start is None:
            return cleaned_data
        if to_value and end is None:
            return cleaned_data
        if end is None:
            end = self._event_end_utc()
        if end <= start:
            self.add_error("to", "Время окончания должно быть позже времени начала.")
            return cleaned_data
        cleaned_data["utc_bounds"] = (start - timedelta(minutes=10), end + timedelta(minutes=10))
        return cleaned_data

    def _parse_event_time(self, value: str, field_name: str) -> datetime | None:
        try:
            local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        except ValueError:
            self.add_error(field_name, "Введите дату и время события.")
            return None
        minimum = datetime.combine(self.event.start_date, time.min)
        maximum = datetime.combine(self.event.end_date, time.max)
        if not minimum <= local <= maximum:
            self.add_error(field_name, "Выберите время в пределах дат события.")
            return None
        return self._unambiguous_utc(local, field_name)

    def _event_end_utc(self) -> datetime:
        return (
            datetime.combine(self.event.end_date + timedelta(days=1), time.min)
            .replace(tzinfo=ZoneInfo(self.event.timezone_name))
            .astimezone(UTC)
        )

    def _unambiguous_utc(self, local: datetime, field_name: str) -> datetime | None:
        zone = ZoneInfo(self.event.timezone_name)
        first = local.replace(tzinfo=zone, fold=0)
        second = local.replace(tzinfo=zone, fold=1)
        first_utc = first.astimezone(UTC)
        second_utc = second.astimezone(UTC)
        first_valid = first_utc.astimezone(zone).replace(tzinfo=None) == local
        second_valid = second_utc.astimezone(zone).replace(tzinfo=None) == local
        if not first_valid and not second_valid:
            self.add_error(field_name, "Такого времени в часовом поясе события не существует.")
            return None
        if first_valid and second_valid and first_utc != second_utc:
            self.add_error(field_name, "Выберите однозначное время в часовом поясе события.")
            return None
        return first_utc if first_valid else second_utc
