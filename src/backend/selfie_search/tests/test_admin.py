from datetime import date
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ingestion.storage import ObjectMissing, StorageUnavailable
from picflow.models import Event
from selfie_search.models import SelfieSearch, SelfieSearchFeedback, SelfieSearchFeedbackAccessAudit
from selfie_search.storage import DownloadGrant


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class SelfieSearchFeedbackAdminTests(TestCase):
    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Feedback admin event",
            slug="feedback-admin-event",
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
            city="Moscow",
        )
        self.feedback = self.make_feedback()
        self.staff = get_user_model().objects.create_user(
            username="feedback-staff",
            password="password",
            is_staff=True,
        )
        self.non_staff = get_user_model().objects.create_user(
            username="feedback-customer",
            password="password",
        )
        self.view_permission = Permission.objects.get(
            content_type__app_label="selfie_search",
            codename="view_selfiesearchfeedback",
        )
        self.change_permission = Permission.objects.get(
            content_type__app_label="selfie_search",
            codename="change_selfiesearchfeedback",
        )
        self.sensitive_permission = Permission.objects.get(
            content_type__app_label="selfie_search",
            codename="view_sensitive_feedback",
        )

    def make_feedback(self) -> SelfieSearchFeedback:
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="selfie-search/feedback-admin.jpg",
            configuration={"embedding_model": "sface"},
            status=SelfieSearch.Status.FAILED,
        )
        return SelfieSearchFeedback.objects.create(
            search=search,
            variant=SelfieSearchFeedback.Variant.PROBLEM,
            contact="contact@example.test",
            personal_data_consent=True,
            consent_text_version="2026-08-04",
            consented_at=timezone.now(),
            source_status=SelfieSearch.Status.FAILED,
            source_configuration={"embedding_model": "sface"},
            object_key="abcdefabcdefabcdefabcdefabcdefab",
            object_content_type="image/jpeg",
            object_size=1,
            object_uploaded_at=timezone.now(),
        )

    def grant_sensitive_access(self, user) -> None:
        user.user_permissions.add(self.view_permission, self.sensitive_permission)

    def action_url(self, action: str) -> str:
        return reverse(
            f"admin:selfie_search_selfiesearchfeedback_{action}",
            args=(self.feedback.pk,),
        )

    def test_anonymous_and_non_staff_cannot_use_sensitive_actions(self) -> None:
        contact_url = self.action_url("view_contact")

        anonymous = self.client.post(contact_url)
        self.assertEqual(anonymous.status_code, 302)

        self.client.force_login(self.non_staff)
        non_staff = self.client.post(contact_url)
        self.assertEqual(non_staff.status_code, 302)
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    def test_staff_without_required_permissions_cannot_use_sensitive_actions(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.post(self.action_url("view_contact"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    def test_staff_with_model_view_but_without_sensitive_permission_is_denied(self) -> None:
        self.staff.user_permissions.add(self.view_permission)
        self.client.force_login(self.staff)

        response = self.client.post(self.action_url("view_contact"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    def test_change_permission_does_not_replace_required_model_view_permission(self) -> None:
        self.staff.user_permissions.add(self.change_permission, self.sensitive_permission)
        self.client.force_login(self.staff)

        response = self.client.post(self.action_url("view_contact"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    def test_authorized_contact_action_reveals_contact_and_writes_sanitized_audit(self) -> None:
        self.grant_sensitive_access(self.staff)
        self.client.force_login(self.staff)

        response = self.client.post(self.action_url("view_contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.feedback.contact)
        self.assertNotContains(response, self.feedback.object_key)
        audit = SelfieSearchFeedbackAccessAudit.objects.get()
        self.assertEqual(audit.feedback, self.feedback)
        self.assertEqual(audit.staff, self.staff)
        self.assertEqual(audit.action, SelfieSearchFeedbackAccessAudit.Action.CONTACT_VIEW)
        self.assertNotIn(self.feedback.contact, str(audit))
        self.assertNotIn(self.feedback.object_key, str(audit))

    @patch("selfie_search.admin.FeedbackSelfieStorage")
    def test_authorized_selfie_action_issues_exact_object_grant_and_writes_audit(
        self, storage_class: Mock
    ) -> None:
        self.grant_sensitive_access(self.staff)
        self.client.force_login(self.staff)
        storage = storage_class.return_value
        storage.create_download_grant.return_value = DownloadGrant(
            url="https://storage.example.test/signed-grant",
            expires_at=timezone.now(),
        )

        response = self.client.post(self.action_url("view_selfie"))

        self.assertRedirects(
            response,
            "https://storage.example.test/signed-grant",
            fetch_redirect_response=False,
        )
        storage.inspect.assert_called_once_with(key=self.feedback.object_key)
        storage.create_download_grant.assert_called_once_with(key=self.feedback.object_key)
        audit = SelfieSearchFeedbackAccessAudit.objects.get()
        self.assertEqual(audit.action, SelfieSearchFeedbackAccessAudit.Action.SELFIE_VIEW)
        self.assertEqual(audit.feedback, self.feedback)
        self.assertEqual(audit.staff, self.staff)

    @patch("selfie_search.admin.FeedbackSelfieStorage")
    def test_sensitive_actions_require_csrf_before_access_or_audit(
        self, storage_class: Mock
    ) -> None:
        self.grant_sensitive_access(self.staff)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)

        response = csrf_client.post(self.action_url("view_selfie"))

        self.assertEqual(response.status_code, 403)
        storage_class.assert_not_called()
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    @patch("selfie_search.admin.FeedbackSelfieStorage")
    def test_expired_selfie_renders_expected_unavailable_state_without_audit(
        self, storage_class: Mock
    ) -> None:
        self.grant_sensitive_access(self.staff)
        self.client.force_login(self.staff)
        storage_class.return_value.inspect.side_effect = ObjectMissing()

        response = self.client.post(self.action_url("view_selfie"))

        self.assertEqual(response.status_code, 410)
        self.assertContains(response, "Селфи удалено", status_code=410)
        storage_class.return_value.create_download_grant.assert_not_called()
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    @patch("selfie_search.admin.FeedbackSelfieStorage")
    def test_storage_failure_renders_retryable_sanitized_error_without_audit(
        self, storage_class: Mock
    ) -> None:
        self.grant_sensitive_access(self.staff)
        self.client.force_login(self.staff)
        storage_class.return_value.inspect.side_effect = StorageUnavailable()

        response = self.client.post(self.action_url("view_selfie"))

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "Не удалось загрузить селфи. Попробуйте ещё раз.",
            status_code=503,
        )
        self.assertNotContains(response, self.feedback.object_key, status_code=503)
        self.assertEqual(SelfieSearchFeedbackAccessAudit.objects.count(), 0)

    def test_list_and_search_do_not_expose_contact_or_object_key(self) -> None:
        self.staff.user_permissions.add(self.view_permission)
        self.client.force_login(self.staff)
        list_url = reverse("admin:selfie_search_selfiesearchfeedback_changelist")

        list_response = self.client.get(list_url)
        search_response = self.client.get(list_url, {"q": self.feedback.contact})

        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, self.feedback.contact)
        self.assertNotContains(list_response, self.feedback.object_key)
        self.assertEqual(search_response.context["cl"].result_count, 0)

    def test_detail_hides_sensitive_values_and_shows_actions_only_to_sensitive_staff(self) -> None:
        self.staff.user_permissions.add(self.view_permission)
        self.client.force_login(self.staff)
        detail_url = reverse(
            "admin:selfie_search_selfiesearchfeedback_change", args=(self.feedback.pk,)
        )

        ordinary_response = self.client.get(detail_url)

        self.assertEqual(ordinary_response.status_code, 200)
        self.assertNotContains(ordinary_response, self.feedback.contact)
        self.assertNotContains(ordinary_response, self.feedback.object_key)
        self.assertNotContains(ordinary_response, self.action_url("view_contact"))

        self.staff.user_permissions.add(self.sensitive_permission)
        sensitive_response = self.client.get(detail_url)

        self.assertContains(sensitive_response, self.action_url("view_contact"))
        self.assertContains(sensitive_response, self.action_url("view_selfie"))

    def test_feedback_admin_is_registered_and_immutable(self) -> None:
        model_admin = admin.site._registry[SelfieSearchFeedback]

        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))
        self.assertNotIn("contact", model_admin.list_display)
        self.assertNotIn("object_key", model_admin.list_display)
        self.assertNotIn("contact", model_admin.search_fields)
        self.assertNotIn("object_key", model_admin.search_fields)
