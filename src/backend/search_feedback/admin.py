from django.contrib import admin

from search_feedback.models import SearchFeedback


@admin.register(SearchFeedback)
class SearchFeedbackAdmin(admin.ModelAdmin):
    list_display = ("created_at", "search_type", "value", "event_id", "photo_id")
    list_filter = ("search_type", "value", "created_at", "event_id")
    readonly_fields = (
        "submission_id",
        "event_id",
        "photo_id",
        "search_type",
        "value",
        "search_context",
        "legacy_attachment_id",
        "created_at",
    )
    actions = None

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False
