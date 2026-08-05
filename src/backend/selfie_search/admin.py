from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from ingestion.storage import ObjectMissing, StorageUnavailable

from selfie_search.models import (
    SelfieSearch,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
    SelfieSearchFeedback,
    SelfieSearchFeedbackAccessAudit,
    SelfieSearchFeedbackLabel,
    SelfieSearchResult,
)
from selfie_search.storage import FeedbackSelfieStorage


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


class FeedbackLabelInline(ReadOnlyInline):
    model = SelfieSearchFeedbackLabel
    fields = ("result", "value")
    readonly_fields = fields


class FeedbackAccessAuditInline(ReadOnlyInline):
    model = SelfieSearchFeedbackAccessAudit
    fields = ("staff", "action", "created_at")
    readonly_fields = fields


class ProvenanceReadOnlyAdmin(admin.ModelAdmin):
    """Expose only bounded provenance summaries; never expose biometric identities."""

    actions = None
    search_fields = ()

    def has_add_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False


@admin.register(SelfieSearch)
class SelfieSearchAdmin(ProvenanceReadOnlyAdmin):
    list_display = (
        "status",
        "matched_photo_count",
        "final_matched_photo_count",
        "direct_matched_photo_count",
        "cluster_expanded_photo_count",
        "cluster_expansion_outcome",
        "created_at",
    )
    list_filter = ("status", "cluster_expansion_outcome")
    readonly_fields = list_display
    fields = readonly_fields


@admin.register(SelfieSearchResult)
class SelfieSearchResultAdmin(ProvenanceReadOnlyAdmin):
    list_display = ("primary_source", "rank", "created_at")
    list_filter = ("primary_source",)
    readonly_fields = list_display
    fields = readonly_fields


@admin.register(SelfieSearchDirectEvidence)
class SelfieSearchDirectEvidenceAdmin(ProvenanceReadOnlyAdmin):
    list_display = ("created_at",)
    readonly_fields = list_display
    fields = readonly_fields


@admin.register(SelfieSearchClusterEvidence)
class SelfieSearchClusterEvidenceAdmin(ProvenanceReadOnlyAdmin):
    list_display = ("created_at",)
    readonly_fields = list_display
    fields = readonly_fields


@admin.register(SelfieSearchFeedback)
class SelfieSearchFeedbackAdmin(admin.ModelAdmin):
    change_form_template = "admin/selfie_search/selfiesearchfeedback/change_form.html"
    list_display = (
        "id",
        "search",
        "variant",
        "source_status",
        "source_matched_photo_count",
        "source_visible_result_count",
        "created_at",
    )
    list_filter = ("variant", "source_status", "created_at")
    search_fields = ("id", "search__id")
    ordering = ("-created_at",)
    fields = (
        "id",
        "search",
        "variant",
        "source_status",
        "source_matched_photo_count",
        "source_visible_result_count",
        "source_configuration",
        "personal_data_consent",
        "consent_text_version",
        "consented_at",
        "object_content_type",
        "object_size",
        "object_uploaded_at",
        "created_at",
    )
    readonly_fields = fields
    inlines = (FeedbackLabelInline, FeedbackAccessAuditInline)

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    def get_urls(self):
        info = self.opts.app_label, self.opts.model_name
        return [
            path(
                "<path:object_id>/view-contact/",
                self.admin_site.admin_view(self.view_contact),
                name=f"{info[0]}_{info[1]}_view_contact",
            ),
            path(
                "<path:object_id>/view-selfie/",
                self.admin_site.admin_view(self.view_selfie),
                name=f"{info[0]}_{info[1]}_view_selfie",
            ),
        ] + super().get_urls()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj is not None and self._can_view_sensitive_feedback(request):
            context["sensitive_feedback_action_urls"] = {
                "contact": reverse(
                    "admin:selfie_search_selfiesearchfeedback_view_contact", args=(obj.pk,)
                ),
                "selfie": reverse(
                    "admin:selfie_search_selfiesearchfeedback_view_selfie", args=(obj.pk,)
                ),
            }
        return super().render_change_form(request, context, add, change, form_url, obj)

    def view_contact(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        feedback = self._sensitive_feedback(request, object_id)
        SelfieSearchFeedbackAccessAudit.objects.create(
            feedback=feedback,
            staff=request.user,
            action=SelfieSearchFeedbackAccessAudit.Action.CONTACT_VIEW,
        )
        return HttpResponse(feedback.contact, content_type="text/plain; charset=utf-8")

    def view_selfie(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        feedback = self._sensitive_feedback(request, object_id)
        storage = FeedbackSelfieStorage()
        try:
            storage.inspect(key=feedback.object_key)
            grant = storage.create_download_grant(key=feedback.object_key)
        except ObjectMissing:
            return HttpResponse("Селфи удалено", status=410)
        except StorageUnavailable:
            return HttpResponse(
                "Не удалось загрузить селфи. Попробуйте ещё раз.",
                status=503,
            )
        SelfieSearchFeedbackAccessAudit.objects.create(
            feedback=feedback,
            staff=request.user,
            action=SelfieSearchFeedbackAccessAudit.Action.SELFIE_VIEW,
        )
        return HttpResponseRedirect(grant.url)

    def _can_view_sensitive_feedback(self, request) -> bool:
        return (
            request.user.is_staff
            and request.user.has_perm("selfie_search.view_selfiesearchfeedback")
            and request.user.has_perm("selfie_search.view_sensitive_feedback")
        )

    def _sensitive_feedback(self, request, object_id) -> SelfieSearchFeedback:
        if not self._can_view_sensitive_feedback(request):
            raise PermissionDenied
        feedback = self.get_object(request, object_id)
        if feedback is None:
            raise PermissionDenied
        return feedback
