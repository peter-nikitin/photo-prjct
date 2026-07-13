from django.urls import path

from ingestion import views

urlpatterns = [
    path("login/", views.PhotographerLoginView.as_view(), name="photographer_login"),
    path("logout/", views.PhotographerLogoutView.as_view(), name="photographer_logout"),
    path("uploads/", views.upload_page, name="upload_page"),
    path("uploads/batches/", views.upload_batch_create, name="upload_batch_create"),
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
