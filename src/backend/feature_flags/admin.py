from django.contrib import admin

from feature_flags.models import FeatureFlag


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ("key", "description", "state", "updated_at")
    list_filter = ("state",)
    search_fields = ("key", "description")
    readonly_fields = ("created_at", "updated_at")
