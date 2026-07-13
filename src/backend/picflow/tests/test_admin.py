from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, modify_settings, override_settings
from django.urls import reverse

from picflow.models import Event


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class EventAdminTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_superuser(
            "admin", "admin@example.com", "password"
        )
        self.client.force_login(self.user)

    def test_admin_creates_and_publishes_event(self) -> None:
        response = self.client.post(
            reverse("admin:picflow_event_add"),
            {
                "name": "Admin Run",
                "slug": "admin-run",
                "start_date": date.today(),
                "end_date": date.today(),
                "city": "Moscow",
                "description": "Created in admin",
                "access_type": Event.AccessType.FREE,
                "publication_status": Event.PublicationStatus.PUBLISHED,
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.published().filter(slug="admin-run").exists())

    def test_admin_rejects_invalid_dates(self) -> None:
        response = self.client.post(
            reverse("admin:picflow_event_add"),
            {
                "name": "Invalid",
                "slug": "invalid",
                "start_date": date.today(),
                "end_date": date.today() - timedelta(days=1),
                "city": "Moscow",
                "access_type": Event.AccessType.FREE,
                "publication_status": Event.PublicationStatus.DRAFT,
            },
        )
        self.assertContains(response, "End date cannot be earlier than start date")
        self.assertFalse(Event.objects.filter(slug="invalid").exists())

    def test_admin_disallows_event_deletion(self) -> None:
        event = Event.objects.create(
            name="Run", slug="run", start_date=date.today(), end_date=date.today(), city="Moscow"
        )
        response = self.client.get(reverse("admin:picflow_event_delete", args=[event.pk]))
        self.assertEqual(response.status_code, 403)
