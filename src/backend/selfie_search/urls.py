from django.urls import path

from selfie_search import views

app_name = "selfie_search"

urlpatterns = [
    path("events/<str:event_slug>/selfie-search/", views.submit, name="submit"),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/",
        views.result,
        name="result",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/status/",
        views.status,
        name="status",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/photos/<str:photo_id>/media/<str:variant>/",
        views.result_media,
        name="result_media",
    ),
]
