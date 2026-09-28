from django.urls import path

from processing import views

urlpatterns = [
    path("members/telemetry", views.member_telemetry, name="processing_member_telemetry"),
    path("members/register", views.member_register, name="processing_member_register"),
    path("members/heartbeat", views.member_heartbeat, name="processing_member_heartbeat"),
    path("members/retire", views.member_retire, name="processing_member_retire"),
    path("claim", views.claim, name="processing_claim"),
    path("attempts/<str:attempt_id>/heartbeat", views.heartbeat, name="processing_heartbeat"),
    path("attempts/<str:attempt_id>/download", views.refresh_download, name="processing_download"),
    path("attempts/<str:attempt_id>/complete", views.complete, name="processing_complete"),
    path("attempts/<str:attempt_id>/fail", views.fail, name="processing_fail"),
]
