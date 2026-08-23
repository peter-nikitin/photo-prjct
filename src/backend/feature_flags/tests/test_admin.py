from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, modify_settings, override_settings
from django.urls import reverse

from feature_flags.models import FeatureFlag


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class FeatureFlagAdminTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="feature-operator", password="password", is_staff=True
        )
        self.flag = FeatureFlag.objects.create(
            key="admin-release", description="Admin-managed release", state=FeatureFlag.State.OFF
        )
        self.model_admin = admin.site._registry[FeatureFlag]
        self.view_permission = Permission.objects.get(
            content_type__app_label="feature_flags", codename="view_featureflag"
        )
        self.change_permission = Permission.objects.get(
            content_type__app_label="feature_flags", codename="change_featureflag"
        )

    def test_view_and_change_permissions_follow_django_model_permissions(self) -> None:
        self.client.force_login(self.user)
        change_url = reverse("admin:feature_flags_featureflag_change", args=(self.flag.pk,))

        self.assertEqual(self.client.get(change_url).status_code, 403)
        self.user.user_permissions.add(self.view_permission)
        self.assertEqual(self.client.get(change_url).status_code, 200)
        self.assertFalse(self.model_admin.has_change_permission(self.request()))
        self.user.user_permissions.add(self.change_permission)
        self.assertTrue(self.model_admin.has_change_permission(self.request()))

    def test_admin_change_records_standard_history_entry(self) -> None:
        self.user.user_permissions.add(self.change_permission)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin:feature_flags_featureflag_change", args=(self.flag.pk,)),
            {
                "key": self.flag.key,
                "description": "An attempted definition edit",
                "state": FeatureFlag.State.STAFF,
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.flag.refresh_from_db()
        self.assertEqual(self.flag.state, FeatureFlag.State.STAFF)
        self.assertEqual(self.flag.key, "admin-release")
        self.assertEqual(self.flag.description, "Admin-managed release")
        history = LogEntry.objects.get(object_id=str(self.flag.pk), action_flag=CHANGE)
        self.assertEqual(history.user, self.user)
        self.assertEqual(history.content_type.app_label, "feature_flags")

    def test_admin_disallows_adding_and_deleting_feature_definitions(self) -> None:
        self.user.user_permissions.add(self.change_permission)
        self.client.force_login(self.user)

        self.assertFalse(self.model_admin.has_add_permission(self.request()))
        self.assertFalse(self.model_admin.has_delete_permission(self.request(), self.flag))
        self.assertEqual(
            self.client.get(reverse("admin:feature_flags_featureflag_add")).status_code, 403
        )
        self.assertEqual(
            self.client.get(
                reverse("admin:feature_flags_featureflag_delete", args=(self.flag.pk,))
            ).status_code,
            403,
        )

    def request(self):
        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.get(pk=self.user.pk)
        return request
