from django.urls import path

from ingestion import import_views, views

urlpatterns = [
    path("login/", views.PhotographerLoginView.as_view(), name="photographer_login"),
    path("logout/", views.PhotographerLogoutView.as_view(), name="photographer_logout"),
    path("uploads/", views.upload_page, name="upload_page"),
    path("uploads/imports/", import_views.import_collection, name="import_collection"),
    path(
        "uploads/imports/<uuid:batch>/",
        import_views.import_detail,
        name="import_detail",
    ),
    path(
        "uploads/imports/<uuid:batch>/items/",
        import_views.import_items,
        name="import_items",
    ),
    path(
        "uploads/imports/<uuid:batch>/retry/",
        import_views.import_retry,
        name="import_retry",
    ),
    path("uploads/batches/", views.upload_batch_create, name="upload_batch_create"),
    path(
        "uploads/<uuid:batch>/resume/",
        views.upload_batch_resume_manifest,
        name="upload_batch_resume_manifest",
    ),
    path(
        "uploads/<uuid:batch>/items/",
        views.upload_items_register,
        name="upload_items_register",
    ),
    path(
        "uploads/<uuid:batch>/items/<uuid:item>/authorize/",
        views.upload_item_authorize,
        name="upload_item_authorize",
    ),
    path(
        "uploads/<uuid:batch>/items/<uuid:item>/retry/",
        views.upload_item_retry,
        name="upload_item_retry",
    ),
    path(
        "uploads/<uuid:batch>/items/<uuid:item>/confirm/",
        views.upload_item_confirm,
        name="upload_item_confirm",
    ),
    path(
        "uploads/<uuid:batch>/items/<uuid:item>/failed/",
        views.upload_item_failed,
        name="upload_item_failed",
    ),
    path(
        "uploads/<uuid:batch>/finalize/",
        views.upload_batch_finalize,
        name="upload_batch_finalize",
    ),
]
