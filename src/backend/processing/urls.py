from django.urls import path

from processing import views

urlpatterns = [
    path("claim", views.claim, name="processing_claim"),
    path("attempts/<str:attempt_id>/heartbeat", views.heartbeat, name="processing_heartbeat"),
    path("attempts/<str:attempt_id>/download", views.refresh_download, name="processing_download"),
    path("attempts/<str:attempt_id>/complete", views.complete, name="processing_complete"),
    path("attempts/<str:attempt_id>/fail", views.fail, name="processing_fail"),
]
