from django.urls import path

from picflow.event_management_media import event_management_media
from picflow.event_management_status import event_management_status
from picflow.event_management_views import (
    event_management,
    event_management_action,
    event_management_batch_history,
    event_management_folder_create,
    event_management_folder_delete,
    event_management_folder_rename,
    event_management_results,
)

urlpatterns = [
    path("manage/events/<int:event_id>/photos/", event_management, name="event_management"),
    path(
        "manage/events/<int:event_id>/photos/results/",
        event_management_results,
        name="event_management_results",
    ),
    path(
        "manage/events/<int:event_id>/photos/batch-history/",
        event_management_batch_history,
        name="event_management_batch_history",
    ),
    path(
        "manage/events/<int:event_id>/photos/folders/create/",
        event_management_folder_create,
        name="event_management_folder_create",
    ),
    path(
        "manage/events/<int:event_id>/photos/folders/rename/",
        event_management_folder_rename,
        name="event_management_folder_rename",
    ),
    path(
        "manage/events/<int:event_id>/photos/folders/delete/",
        event_management_folder_delete,
        name="event_management_folder_delete",
    ),
    path(
        "manage/events/<int:event_id>/photos/actions/",
        event_management_action,
        name="event_management_action",
    ),
    path(
        "manage/events/<int:event_id>/photos/status/",
        event_management_status,
        name="event_management_status",
    ),
    path(
        "manage/events/<int:event_id>/photos/<str:photo_id>/media/<str:variant>/",
        event_management_media,
        name="event_management_media",
    ),
]
