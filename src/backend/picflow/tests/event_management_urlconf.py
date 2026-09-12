from django.urls import include, path

urlpatterns = [
    path("", include("picflow.event_management_urls")),
    path("", include("config.urls")),
]
