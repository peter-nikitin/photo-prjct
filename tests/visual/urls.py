from django.urls import include, path

from tests.visual import views

visual_patterns = [
    path("catalog/populated/", views.catalog_populated, name="visual_catalog_populated"),
    path(
        "catalog/staff-preview/",
        views.catalog_staff_preview,
        name="visual_catalog_staff_preview",
    ),
    path("catalog/empty/", views.catalog_empty, name="visual_catalog_empty"),
    path("event/covered/", views.event_covered, name="visual_event_covered"),
    path("event/uncovered/", views.event_uncovered, name="visual_event_uncovered"),
    path(
        "event/gallery-populated/",
        views.event_gallery_populated,
        name="visual_event_gallery_populated",
    ),
    path(
        "event/gallery-paid/",
        views.event_gallery_paid,
        name="visual_event_gallery_paid",
    ),
    path("event/cart/", views.cart_populated, name="visual_cart_populated"),
    path("event/cart/empty/", views.cart_empty, name="visual_cart_empty"),
    path("order/pending/", views.order_pending, name="visual_order_pending"),
    path(
        "order/pending/status/",
        views.order_pending_status,
        name="visual_order_pending_status",
    ),
    path("order/paid/", views.order_paid, name="visual_order_paid"),
    path("order/email-failed/", views.order_email_failed, name="visual_order_email_failed"),
    path(
        "event/gallery-staff-preview/",
        views.event_gallery_staff_preview,
        name="visual_event_gallery_staff_preview",
    ),
    path(
        "event/gallery-empty/",
        views.event_gallery_empty,
        name="visual_event_gallery_empty",
    ),
    path(
        "event/gallery-filtered-empty/",
        views.event_gallery_filtered_empty,
        name="visual_event_gallery_filtered_empty",
    ),
    path(
        "event/gallery-manual-invalid/",
        views.event_gallery_manual_invalid,
        name="visual_event_gallery_manual_invalid",
    ),
    path("event/selfie-search/", views.event_selfie_search, name="visual_event_selfie_search"),
    path(
        "event/selfie-search/rejected/",
        views.event_selfie_search_rejected,
        name="visual_event_selfie_search_rejected",
    ),
    path(
        "event/selfie-search/processing/",
        views.selfie_search_processing,
        name="visual_selfie_search_processing",
    ),
    path(
        "event/selfie-search/processing-status/",
        views.selfie_search_processing_status,
        name="visual_selfie_search_processing_status",
    ),
    path(
        "event/selfie-search/empty/",
        views.selfie_search_empty,
        name="visual_selfie_search_empty",
    ),
    path(
        "event/selfie-search/error/",
        views.selfie_search_error,
        name="visual_selfie_search_error",
    ),
    path(
        "event/selfie-search/ready/",
        views.selfie_search_ready,
        name="visual_selfie_search_ready",
    ),
    path(
        "event/selfie-search/ready/paid/",
        views.selfie_search_ready_paid,
        name="visual_selfie_search_ready_paid",
    ),
    path(
        "event/selfie-search/ready/staff-preview/",
        views.selfie_search_ready_staff_preview,
        name="visual_selfie_search_ready_staff_preview",
    ),
    path(
        "event/selfie-search/feedback-problem/",
        views.selfie_search_feedback_problem,
        name="visual_selfie_search_feedback_problem",
    ),
    path(
        "event/selfie-search/feedback-marking/",
        views.selfie_search_feedback_marking,
        name="visual_selfie_search_feedback_marking",
    ),
    path("legal/", views.legal, name="visual_legal"),
    path("reference/search/", views.reference_search, name="visual_reference_search"),
    path(
        "reference/dashboard/",
        views.reference_dashboard,
        name="visual_reference_dashboard",
    ),
    path("reference/events/", views.reference_events, name="visual_reference_events"),
    path("upload/empty/", views.upload_empty, name="visual_upload_empty"),
    path("upload/active/", views.upload_active, name="visual_upload_active"),
    path("upload/partial/", views.upload_partial, name="visual_upload_partial"),
    path("upload/complete/", views.upload_complete, name="visual_upload_complete"),
    path("upload/folders/", views.upload_folders, name="visual_upload_folders"),
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
    path("events/london-10k/", views.visual_event_detail, name="visual_event_detail"),
    path("", include("config.urls")),
]
