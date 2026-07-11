from django.contrib import admin
from django.urls import path

from config import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.index, name="index"),
    path("events/", views.events, name="events"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("upload/", views.upload, name="upload"),
    path("orders/", views.orders, name="orders"),
    path("promos/", views.promos, name="promos"),
    path("purchased/", views.purchased, name="purchased"),
    path("legal/", views.legal, name="legal"),
    path("admin/", admin.site.urls),
]
