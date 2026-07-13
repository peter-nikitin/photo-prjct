from django.contrib import admin

from ingestion.models import UploadBatch, UploadItem


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(UploadBatch)
class UploadBatchAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "status",
        "event",
        "uploader",
        "expected_item_count",
        "created_at",
        "last_activity_at",
    )
    list_filter = ("status", "event", "uploader", "created_at")
    search_fields = ("id", "uploader__username", "event__name")
    fields = (
        "id",
        "event",
        "uploader",
        "expected_item_count",
        "status",
        "created_at",
        "last_activity_at",
        "completed_at",
    )
    readonly_fields = fields


@admin.register(UploadItem)
class UploadItemAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "status",
        "batch",
        "original_filename",
        "expected_size",
        "error_code",
        "last_activity_at",
    )
    list_filter = ("status", "batch__event", "batch__uploader", "last_activity_at")
    search_fields = (
        "id",
        "client_item_id",
        "original_filename",
        "error_code",
        "sanitized_error_message",
    )
    fields = (
        "id",
        "batch",
        "client_item_id",
        "original_filename",
        "declared_content_type",
        "expected_size",
        "status",
        "verified_source_etag",
        "error_code",
        "sanitized_error_message",
        "authorization_expires_at",
        "upload_attempts",
        "last_activity_at",
        "completed_at",
        "photo",
    )
    readonly_fields = fields
