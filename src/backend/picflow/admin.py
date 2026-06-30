from django.contrib import admin

from picflow.models import Event, Photo


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "date_from", "date_to", "location", "active")
    list_filter = ("active",)
    search_fields = ("name", "location", "description")


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "src")
    list_filter = ("event",)
    search_fields = ("id",)
