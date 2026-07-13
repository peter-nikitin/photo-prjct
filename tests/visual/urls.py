from django.urls import include, path

from tests.visual import views

visual_patterns = [
    path("catalog/populated/", views.catalog_populated, name="visual_catalog_populated"),
    path("catalog/empty/", views.catalog_empty, name="visual_catalog_empty"),
    path("event/covered/", views.event_covered, name="visual_event_covered"),
    path("event/uncovered/", views.event_uncovered, name="visual_event_uncovered"),
    path("legal/", views.legal, name="visual_legal"),
    path("reference/search/", views.reference_search, name="visual_reference_search"),
    path(
        "reference/dashboard/",
        views.reference_dashboard,
        name="visual_reference_dashboard",
    ),
    path("reference/events/", views.reference_events, name="visual_reference_events"),
    path("reference/upload/", views.reference_upload, name="visual_reference_upload"),
    path("reference/orders/", views.reference_orders, name="visual_reference_orders"),
    path(
        "reference/promotions/",
        views.reference_promotions,
        name="visual_reference_promotions",
    ),
    path("reference/purchased/", views.reference_purchased, name="visual_reference_purchased"),
]

urlpatterns = [
    path("__visual__/", include(visual_patterns)),
    path("", include("config.urls")),
]
