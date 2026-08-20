from django.urls import path

from commerce import views

app_name = "commerce"

urlpatterns = [
    path("events/<str:event_slug>/cart/", views.detail, name="detail"),
    path("events/<str:event_slug>/cart/clear/", views.clear, name="clear"),
    path(
        "events/<str:event_slug>/cart/state/",
        views.set_photo_state,
        name="set_photo_state",
    ),
]
