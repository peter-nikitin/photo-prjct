from django.contrib import admin
from django.urls import include, path
from processing.admin_progress import admin_processing_progress

from config import views

urlpatterns = [
    path("internal/photo-processing/v1/", include("processing.urls")),
    path("health/", views.health, name="health"),
    path("", views.event_catalog, name="event_catalog"),
    path("events/", views.legacy_events_redirect, name="legacy_events"),
    path(
        "events/<str:slug>/photos/<str:photo_id>/media/<str:variant>/",
        views.photo_media,
        name="photo_media",
    ),
    path("events/<str:slug>/", views.event_detail, name="event_detail"),
    path("legal/", views.legal, name="legal"),
    path("photographer/", include("ingestion.urls")),
    path("admin/processing/", admin_processing_progress, name="admin_processing_progress"),
    path("admin/", admin.site.urls),
]
