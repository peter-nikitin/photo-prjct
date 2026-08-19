from collections.abc import Iterable

from django.http import HttpRequest

from picflow.models import Event

_STAFF_PREVIEW_REQUEST_ATTRIBUTE = "_is_event_staff_preview"


def mark_event_staff_preview(request: HttpRequest, events: Iterable[Event]) -> None:
    if any(event.publication_status == Event.PublicationStatus.DRAFT for event in events):
        setattr(request, _STAFF_PREVIEW_REQUEST_ATTRIBUTE, True)


def is_event_staff_preview(request: HttpRequest) -> bool:
    return bool(getattr(request, _STAFF_PREVIEW_REQUEST_ATTRIBUTE, False))
