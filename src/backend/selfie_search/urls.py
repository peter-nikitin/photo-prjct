from django.urls import path

from selfie_search import views

app_name = "selfie_search"

urlpatterns = [
    path(
        "events/<str:event_slug>/photos/<str:photo_id>/similar-search/<uuid:detection_id>/",
        views.submit_gallery_search,
        name="submit_gallery_face",
    ),
    path(
        "events/<str:event_slug>/photos/<str:photo_id>/similar-search/",
        views.submit_gallery_search,
        name="submit_gallery_apparel",
    ),
    path("events/<str:event_slug>/selfie-search/", views.submit, name="submit"),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/",
        views.result,
        name="result",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/continue/",
        views.continue_search,
        name="continue",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/download/",
        views.result_archive,
        name="result_archive",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/status/",
        views.status,
        name="status",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/feedback-selfie/",
        views.feedback_selfie,
        name="feedback_selfie",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/photos/<str:photo_id>/media/<str:variant>/",
        views.result_media,
        name="result_media",
    ),
    path(
        "events/<str:event_slug>/selfie-search/<str:public_token>/photos/<str:photo_id>/download/",
        views.result_download,
        name="result_download",
    ),
]
