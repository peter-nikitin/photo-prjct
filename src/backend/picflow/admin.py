from django.contrib import admin

from picflow.models import Event, Photo


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "start_date",
        "end_date",
        "city",
        "access_type",
        "publication_status",
    )
    list_filter = ("publication_status", "access_type", "city")
    search_fields = ("name", "city", "description")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        ("Content", {"fields": ("name", "slug", "description", "cover")}),
        ("Schedule", {"fields": ("start_date", "end_date", "city")}),
        ("Access and publication", {"fields": ("access_type", "publication_status")}),
    )

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "src")
    list_filter = ("event",)
    search_fields = ("id",)
