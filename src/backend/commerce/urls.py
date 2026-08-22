from django.urls import path

from commerce import views

app_name = "commerce"

urlpatterns = [
    path(
        "payments/simulator/<str:provider_payment_id>/",
        views.payment_simulator,
        name="payment_simulator",
    ),
    path("events/<str:event_slug>/cart/", views.detail, name="detail"),
    path("events/<str:event_slug>/cart/checkout/", views.checkout, name="checkout"),
    path("orders/<str:public_number>/return/", views.order_return, name="order_return"),
    path("orders/<str:public_number>/status/", views.order_status, name="order_status"),
    path(
        "orders/<str:public_number>/photos/<str:photo_id>/download/",
        views.order_download,
        name="order_download",
    ),
    path(
        "orders/<str:public_number>/photos/<str:photo_id>/media/<str:variant>/",
        views.order_media,
        name="order_media",
    ),
    path("orders/<str:public_number>/resend/", views.order_resend, name="order_resend"),
    path("orders/<str:public_number>/", views.order, name="order"),
    path(
        "orders/<str:public_number>/access/<str:grant_identifier>/<str:signature>/",
        views.grant_order,
        name="grant_order",
    ),
    path(
        "orders/<str:public_number>/access/<str:grant_identifier>/<str:signature>/status/",
        views.grant_order_status,
        name="grant_order_status",
    ),
    path(
        "orders/<str:public_number>/access/<str:grant_identifier>/<str:signature>/photos/<str:photo_id>/download/",
        views.grant_order_download,
        name="grant_order_download",
    ),
    path(
        "orders/<str:public_number>/access/<str:grant_identifier>/<str:signature>/photos/<str:photo_id>/media/<str:variant>/",
        views.grant_order_media,
        name="grant_order_media",
    ),
    path(
        "orders/<str:public_number>/access/<str:grant_identifier>/<str:signature>/resend/",
        views.grant_order_resend,
        name="grant_order_resend",
    ),
    path("events/<str:event_slug>/cart/clear/", views.clear, name="clear"),
    path(
        "events/<str:event_slug>/cart/state/",
        views.set_photo_state,
        name="set_photo_state",
    ),
]
