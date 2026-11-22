from django.contrib import admin, messages
from django.http import HttpRequest
from django.template.response import TemplateResponse

from agent_api.models import AgentToken


@admin.register(AgentToken)
class AgentTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at", "expires_at", "last_used_at", "revoked_at")
    actions = ("revoke_tokens",)

    def get_fields(self, request, obj=None):
        return self.list_display if obj else ("name",)

    def get_readonly_fields(self, request, obj=None):
        return self.list_display if obj else ()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.filter(owner=request.user)

    def has_module_permission(self, request: HttpRequest) -> bool:
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (
            obj is None or request.user.is_superuser or obj.owner_id == request.user.pk
        )

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.owner = request.user
            request.agent_token_secret = obj.prepare()
            super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        response = TemplateResponse(
            request,
            "admin/agent_api/issued_token.html",
            {**self.admin_site.each_context(request), "secret": request.agent_token_secret},
        )
        response["Cache-Control"] = "no-store"
        return response

    @admin.action(description="Отозвать выбранные токены")
    def revoke_tokens(self, request, queryset):
        for token in queryset:
            token.revoke()
        self.message_user(request, "Выбранные токены отозваны.", messages.SUCCESS)
