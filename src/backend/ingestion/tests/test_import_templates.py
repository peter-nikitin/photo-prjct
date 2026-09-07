from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from feature_flags.models import FeatureFlag
from feature_flags.registry import YANDEX_DISK_IMPORT
from ingestion.models import ImportBatch
from picflow.models import Event, EventFolder


@override_settings(
    PHOTO_UPLOAD_ENABLED=True,
    PHOTO_IMPORT_ENABLED=True,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class ImportTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_user(username="photo", password="pass")
        cls.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")
        )
        cls.event = Event.objects.create(
            name="Draft race",
            slug="draft-race",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 7),
            city="Moscow",
            publication_status=Event.PublicationStatus.DRAFT,
        )
        cls.folder = EventFolder.objects.create(event=cls.event, name="Финиш")

    def setUp(self) -> None:
        self.client.force_login(self.user)

    def test_enabled_import_form_uses_event_folders_and_real_browser_api(self) -> None:
        FeatureFlag.objects.create(
            key=YANDEX_DISK_IMPORT.key,
            description=YANDEX_DISK_IMPORT.description,
            state=FeatureFlag.State.ON,
        )

        response = self.client.get(reverse("upload_page"))
        html = response.content.decode()

        self.assertContains(response, "data-import-form")
        self.assertContains(response, 'type="url"')
        self.assertContains(response, "data-import-folder-option")
        self.assertContains(response, f'data-event-id="{self.event.pk}"')
        self.assertContains(response, f'value="{self.folder.pk}"')
        self.assertContains(response, "Без папки")
        self.assertContains(response, "Загрузим только JPEG-файлы из указанной папки, без подпапок")
        self.assertContains(response, 'data-import-collection-url="/photographer/uploads/imports/"')
        self.assertIn("/photographer/uploads/imports/{batch}/items/", html)
        self.assertIn("/photographer/uploads/imports/{batch}/retry/", html)
        self.assertContains(response, 'src="/static/ui/import-coordinator.js"')

    def test_gate_off_hides_new_import_controls_but_keeps_owned_history_readable(self) -> None:
        ImportBatch.objects.create(
            owner=self.user,
            event=self.event,
            folder=None,
            submitted_source_key="public-key",
            submission_key="submission-1",
        )

        response = self.client.get(reverse("upload_page"))

        self.assertNotContains(response, "data-import-form")
        self.assertContains(response, 'data-import-history-enabled="true"')
        self.assertContains(response, 'data-import-enabled="false"')

    def test_import_shell_contains_durable_progress_and_exact_alerts(self) -> None:
        response = self.client.get(reverse("upload_page"))

        self.assertContains(response, "data-import-list")
        self.assertContains(response, "data-import-list-pagination")
        self.assertContains(response, "data-import-card-template")
        self.assertContains(response, "data-import-items-pagination")
        self.assertContains(response, 'data-import-action-status role="status" aria-live="polite"')
        self.assertContains(
            response, "Задача принята. Можно закрыть страницу — загрузка продолжится на сервере"
        )
        self.assertContains(
            response,
            "В папке по ссылке есть вложенные папки. Фотографии из них загружены не будут.",
        )

    def test_revoked_upload_rights_still_deny_the_page(self) -> None:
        self.user.user_permissions.clear()

        response = self.client.get(reverse("upload_page"))

        self.assertEqual(response.status_code, 403)
