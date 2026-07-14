from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from picflow.models import Event


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class UploadTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_user(username="photo", password="pass")
        cls.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")
        )
        cls.event = Event.objects.create(
            name="Draft race",
            slug="draft-race",
            start_date=date(2026, 7, 14),
            end_date=date(2026, 7, 14),
            city="Moscow",
            publication_status=Event.PublicationStatus.DRAFT,
        )

    def setUp(self) -> None:
        self.client.force_login(self.user)

    def test_upload_page_renders_accessible_bounded_queue_shell(self) -> None:
        response = self.client.get(reverse("upload_page"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ingestion/upload.html")
        self.assertContains(response, 'name="event_id"')
        self.assertContains(response, self.event.name)
        self.assertContains(response, 'type="file"')
        self.assertContains(response, 'accept="image/jpeg,.jpg,.jpeg"')
        self.assertContains(response, "multiple")
        self.assertContains(response, "data-upload-drop-target")
        self.assertContains(response, "<progress", html=False)
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, "data-upload-queue")
        self.assertContains(response, "data-retry-item")
        self.assertContains(response, "data-csrf-token=")
        self.assertContains(response, "Закрытие или перезагрузка страницы остановит")
        self.assertContains(response, 'data-queue-window-size="20"')

    def test_upload_page_omits_deferred_controls_claims_and_private_keys(self) -> None:
        response = self.client.get(reverse("upload_page"))
        html = response.content.decode()

        for forbidden in (
            'name="zone"',
            'name="photographer"',
            "Распознавание",
            "QR",
            "EXIF",
            "incoming/",
            "originals/",
            "design-reference",
        ):
            self.assertNotIn(forbidden, html)

    def test_upload_navigation_requires_feature_and_permission(self) -> None:
        response = self.client.get(reverse("event_catalog"))
        self.assertContains(response, reverse("upload_page"))

        self.user.user_permissions.clear()
        response = self.client.get(reverse("event_catalog"))
        self.assertNotContains(response, reverse("upload_page"))

        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")
        )
        with override_settings(PHOTO_UPLOAD_ENABLED=False):
            response = self.client.get(reverse("event_catalog"))
        self.assertNotContains(response, reverse("upload_page"))

    def test_photographer_login_uses_production_template(self) -> None:
        self.client.logout()
        response = self.client.get(reverse("photographer_login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")
        self.assertContains(response, "Вход для фотографов")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
