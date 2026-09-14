"""Domain filters and atomic mutations for the private event photo workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.contrib.auth.models import AbstractUser, AnonymousUser
from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet, Subquery
from django.db.models.deletion import ProtectedError
from processing.photo_status import (
    CATEGORY_PROCESSING,
    CATEGORY_QUEUED,
    annotate_photo_processing_status,
    filter_active_photo_processing_categories,
)

from picflow.event_management_access import can_inspect_event_photos
from picflow.event_management_forms import (
    PROCESSING_CATEGORIES,
    VISIBILITY_HIDDEN,
    VISIBILITY_VISIBLE,
    EventPhotoFilters,
)
from picflow.models import Event, EventFolder, Photo

EVENT_PHOTO_PAGE_SIZE = 100
PhotoAction = Literal["move", "hide", "show"]
SelectionMode = Literal["explicit", "all_filtered"]


@dataclass(frozen=True)
class EventManagementCapabilities:
    can_change_photos: bool
    can_add_folders: bool
    can_change_folders: bool
    can_delete_folders: bool


@dataclass(frozen=True)
class EventPhotoSelection:
    """A fixed ID selection or a filter that is reevaluated at mutation time."""

    mode: SelectionMode
    photo_ids: tuple[str, ...] = ()
    filters: EventPhotoFilters | None = None

    def __post_init__(self) -> None:
        if self.mode == "explicit":
            normalized = tuple(dict.fromkeys(self.photo_ids))
            if not normalized or self.filters is not None:
                raise ValueError("Explicit selection requires photo IDs only.")
            object.__setattr__(self, "photo_ids", normalized)
        elif self.mode == "all_filtered":
            if self.photo_ids or self.filters is None:
                raise ValueError("All-filtered selection requires filters only.")
        else:
            raise ValueError("Unsupported selection mode.")

    @classmethod
    def explicit(cls, photo_ids: tuple[str, ...]) -> EventPhotoSelection:
        return cls(mode="explicit", photo_ids=photo_ids)

    @classmethod
    def all_filtered(cls, filters: EventPhotoFilters) -> EventPhotoSelection:
        return cls(mode="all_filtered", filters=filters)


def event_management_capabilities(
    user: AbstractUser | AnonymousUser,
) -> EventManagementCapabilities:
    """Return mutation capabilities gated by the existing private read boundary."""
    can_inspect = can_inspect_event_photos(user)
    return EventManagementCapabilities(
        can_change_photos=can_inspect and user.has_perm("picflow.change_photo"),
        can_add_folders=can_inspect and user.has_perm("picflow.add_eventfolder"),
        can_change_folders=can_inspect and user.has_perm("picflow.change_eventfolder"),
        can_delete_folders=can_inspect and user.has_perm("picflow.delete_eventfolder"),
    )


def _validate_filter_scope(event: Event, filters: EventPhotoFilters) -> None:
    folder_ids = set(filters.folder_ids)
    if folder_ids:
        valid_folder_ids = set(
            EventFolder.objects.filter(event=event, pk__in=folder_ids).values_list("pk", flat=True)
        )
        if valid_folder_ids != folder_ids:
            raise ValidationError("Unknown event folder filter.")
    uploader_ids = set(filters.uploader_ids)
    if uploader_ids:
        valid_uploader_ids = set(
            Photo.objects.filter(event=event, uploaded_by_id__in=uploader_ids).values_list(
                "uploaded_by_id", flat=True
            )
        )
        if valid_uploader_ids != uploader_ids:
            raise ValidationError("Unknown event uploader filter.")
    if any(category not in PROCESSING_CATEGORIES for category in filters.processing_categories):
        raise ValidationError("Unknown processing filter.")


def event_photo_queryset(event: Event, filters: EventPhotoFilters) -> QuerySet[Photo]:
    """Return all event photos matching one validated administrative filter object."""
    _validate_filter_scope(event, filters)
    photos = Photo.objects.filter(event=event)
    if filters.folder_ids or filters.include_unfiled:
        folder_scope = Q(folder_id__in=filters.folder_ids)
        if filters.include_unfiled:
            folder_scope |= Q(folder__isnull=True)
        photos = photos.filter(folder_scope)
    if filters.uploader_ids or filters.include_unknown_uploader:
        uploader_scope = Q(uploaded_by_id__in=filters.uploader_ids)
        if filters.include_unknown_uploader:
            uploader_scope |= Q(uploaded_by__isnull=True)
        photos = photos.filter(uploader_scope)
    if filters.without_capture_time:
        photos = photos.filter(capture_time__isnull=True)
    elif filters.capture_time_bounds is not None:
        lower, upper = filters.capture_time_bounds
        if lower is not None:
            photos = photos.filter(capture_time__gte=lower)
        if upper is not None:
            photos = photos.filter(capture_time__lte=upper)
    if filters.visibility == VISIBILITY_VISIBLE:
        photos = photos.filter(is_hidden=False)
    elif filters.visibility == VISIBILITY_HIDDEN:
        photos = photos.filter(is_hidden=True)
    if filters.processing_categories:
        selected_processing_categories = frozenset(filters.processing_categories)
        if selected_processing_categories <= {CATEGORY_PROCESSING, CATEGORY_QUEUED}:
            photos = filter_active_photo_processing_categories(
                photos, selected_processing_categories
            )
        else:
            photos = annotate_photo_processing_status(photos).filter(
                processing_category__in=filters.processing_categories
            )
    return photos.order_by(F("capture_time").asc(nulls_last=True), "pk").distinct()


def event_photo_page(
    event: Event,
    filters: EventPhotoFilters,
    *,
    page: str | int = 1,
) -> Page[Photo]:
    """Return one stable 100-photo page for the administrative result set."""
    return Paginator(event_photo_queryset(event, filters), EVENT_PHOTO_PAGE_SIZE).get_page(page)


def _locked_selection_ids(event: Event, selection: EventPhotoSelection) -> tuple[str, ...]:
    if selection.mode == "explicit":
        rows = list(
            Photo.objects.select_for_update()
            .filter(pk__in=selection.photo_ids)
            .values_list("pk", "event_id")
        )
        if len(rows) != len(selection.photo_ids) or any(
            event_id != event.pk for _photo_id, event_id in rows
        ):
            raise ValidationError("Selection contains an unknown or foreign photo.")
        return selection.photo_ids
    if selection.filters is None:
        raise ValidationError("Filtered selection is missing filters.")
    matching_ids = event_photo_queryset(event, selection.filters).order_by().values("pk")
    return tuple(
        Photo.objects.select_for_update()
        .filter(event=event, pk__in=Subquery(matching_ids))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def apply_event_photo_action(
    event: Event,
    selection: EventPhotoSelection,
    action: PhotoAction,
    target_folder: EventFolder | None,
) -> int:
    """Atomically validate and mutate exactly one event-scoped photo selection."""
    if action not in {"move", "hide", "show"}:
        raise ValidationError("Unsupported photo action.")
    if action != "move" and target_folder is not None:
        raise ValidationError("Only a move action accepts a target folder.")
    with transaction.atomic():
        locked_target = None
        if target_folder is not None:
            try:
                locked_target = EventFolder.objects.select_for_update().get(
                    pk=target_folder.pk, event=event
                )
            except EventFolder.DoesNotExist as error:
                raise ValidationError("Target folder does not belong to the event.") from error
        photo_ids = _locked_selection_ids(event, selection)
        selected = Photo.objects.filter(event=event, pk__in=photo_ids)
        if action == "move":
            target_id = locked_target.pk if locked_target is not None else None
            return selected.exclude(folder_id=target_id).update(folder_id=target_id)
        if action == "hide":
            return selected.filter(is_hidden=False).update(is_hidden=True)
        return selected.filter(is_hidden=True).update(is_hidden=False)


def _folder_validation_error(message: str) -> ValidationError:
    return ValidationError({"name": [message]})


def create_event_folder(event: Event, name: str) -> EventFolder:
    """Create one normalized event folder, translating an expected name race."""
    folder = EventFolder(event=event, name=name)
    folder.full_clean()
    try:
        with transaction.atomic():
            folder.save()
    except IntegrityError as error:
        raise _folder_validation_error("Папка с таким названием уже существует.") from error
    return folder


def rename_event_folder(event: Event, folder: EventFolder, name: str) -> EventFolder:
    """Rename only a folder owned by the supplied event."""
    try:
        with transaction.atomic():
            current = EventFolder.objects.select_for_update().get(pk=folder.pk, event=event)
            current.name = name
            current.full_clean()
            current.save(update_fields=["name"])
    except EventFolder.DoesNotExist as error:
        raise ValidationError("Folder does not belong to the event.") from error
    except IntegrityError as error:
        raise _folder_validation_error("Папка с таким названием уже существует.") from error
    return current


def delete_event_folder(event: Event, folder: EventFolder) -> None:
    """Delete only an empty event folder and preserve every protected relation."""
    try:
        with transaction.atomic():
            current = EventFolder.objects.select_for_update().get(pk=folder.pk, event=event)
            current.delete()
    except EventFolder.DoesNotExist as error:
        raise ValidationError("Folder does not belong to the event.") from error
    except ProtectedError as error:
        raise ValidationError("Папка используется и не может быть удалена.") from error
