from django.contrib import admin

from feature_flags.models import FeatureFlag


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ("key", "description", "state", "updated_at")
    list_filter = ("state",)
    search_fields = ("key", "description")
    fields = ("key", "description", "state", "created_at", "updated_at")
    readonly_fields = ("key", "description", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:  # noqa: ANN001
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ANN001
        del obj
        return False
