from __future__ import annotations

from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from ingestion.models import UploadBatch, UploadItem
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

    def test_upload_page_renders_accessible_grouped_queue_shell(self) -> None:
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
        self.assertContains(response, 'data-queue-group-toggle')
        self.assertContains(response, 'data-queue-group-content')
        self.assertContains(
            response, 'data-register-url-template="/photographer/uploads/{batch}/items/"'
        )
        self.assertContains(
            response,
            'data-retry-url-template="/photographer/uploads/{batch}/items/{item}/retry/"',
        )
        self.assertContains(response, 'src="/static/ui/upload-coordinator.js"')

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
            "localStorage",
            "indexedDB",
        ):
            self.assertNotIn(forbidden, html)

    def test_upload_page_context_contains_only_owned_unfinished_batches(self) -> None:
        owned = UploadBatch.objects.create(
            uploader=self.user, event=self.event, expected_item_count=1
        )
        UploadItem.objects.create(
            batch=owned,
            client_item_id=uuid4(),
            original_filename="owned.jpg",
            declared_content_type="image/jpeg",
            expected_size=4,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
        )
        other = get_user_model().objects.create_user(username="other")
        foreign = UploadBatch.objects.create(
            uploader=other,
            event=self.event,
            expected_item_count=1,
        )
        UploadItem.objects.create(
            batch=foreign,
            client_item_id=uuid4(),
            original_filename="foreign.jpg",
            declared_content_type="image/jpeg",
            expected_size=4,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
        )

        response = self.client.get(reverse("upload_page"))

        self.assertEqual(response.context["unfinished_batches"][0].id, owned.id)
        self.assertEqual(len(response.context["unfinished_batches"]), 1)

    def test_upload_page_renders_an_owned_batch_resume_action_and_dedicated_picker(self) -> None:
        batch = UploadBatch.objects.create(
            uploader=self.user, event=self.event, expected_item_count=2
        )
        UploadItem.objects.create(
            batch=batch,
            client_item_id=uuid4(),
            original_filename="waiting.jpg",
            declared_content_type="image/jpeg",
            expected_size=4,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"originals/{uuid4().hex}",
        )

        response = self.client.get(reverse("upload_page"))

        self.assertContains(response, 'data-unfinished-upload')
        self.assertContains(response, f'data-resume-batch-id="{batch.id}"')
        self.assertContains(response, 'data-resume-batch')
        self.assertContains(response, 'id="resume-upload-files"')
        self.assertContains(
            response,
            'data-resume-manifest-url-template="/photographer/uploads/{batch}/resume/"',
        )
        self.assertContains(response, self.event.name)

    def test_upload_navigation_requires_feature_and_permission(self) -> None:
        response = self.client.get(reverse("event_catalog"))
        self.assertContains(response, reverse("upload_page"))
        self.assertContains(response, 'aria-label="Загрузить фотографии"')

        self.user.user_permissions.clear()
        response = self.client.get(reverse("event_catalog"))
        self.assertNotContains(response, reverse("upload_page"))

    def test_upload_template_renders_ordered_groups_with_twenty_item_pages(self) -> None:
        request = RequestFactory().get(reverse("upload_page"))
        request.user = self.user
        item = {
            "name": "",
            "meta": "10 МБ",
            "status": "Ожидает",
            "status_class": "pending",
            "progress": 0,
        }
        queue_groups = [
            {
                "key": "needs_attention",
                "label": "Требуют внимания",
                "expanded": True,
                "count": 25,
                "items": [{**item, "name": f"attention-{index}.jpg"} for index in range(25)],
            },
            {
                "key": "uploading",
                "label": "Загружаются",
                "expanded": True,
                "count": 25,
                "items": [{**item, "name": f"uploading-{index}.jpg"} for index in range(25)],
            },
            {
                "key": "waiting",
                "label": "Ожидают",
                "expanded": False,
                "count": 9_925,
                "items": [{**item, "name": f"waiting-{index}.jpg"} for index in range(9_925)],
            },
            {
                "key": "uploaded",
                "label": "Загружены",
                "expanded": False,
                "count": 25,
                "items": [{**item, "name": f"uploaded-{index}.jpg"} for index in range(25)],
            },
        ]

        html = render_to_string(
            "ingestion/upload.html",
            {
                "events": [self.event],
                "upload_state": "active",
                "upload_queue_groups": queue_groups,
            },
            request=request,
        )

        self.assertEqual(html.count("data-rendered-queue-item"), 40)
        self.assertLess(html.index('data-queue-group="needs_attention"'), html.index('data-queue-group="uploading"'))
        self.assertLess(html.index('data-queue-group="uploading"'), html.index('data-queue-group="waiting"'))
        self.assertLess(html.index('data-queue-group="waiting"'), html.index('data-queue-group="uploaded"'))
        self.assertRegex(
            html, r'data-queue-group-toggle="needs_attention"\s+aria-expanded="true"'
        )
        self.assertRegex(html, r'data-queue-group-toggle="uploading"\s+aria-expanded="true"')
        self.assertRegex(html, r'data-queue-group-toggle="waiting"\s+aria-expanded="false"')
        self.assertRegex(html, r'data-queue-group-toggle="uploaded"\s+aria-expanded="false"')
        self.assertIn("attention-19.jpg", html)
        self.assertNotIn("attention-20.jpg", html)
        self.assertIn("uploading-19.jpg", html)
        self.assertNotIn("uploading-20.jpg", html)
        self.assertNotIn("waiting-0.jpg", html)
        self.assertNotIn("Показаны последние 20 файлов", html)

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
