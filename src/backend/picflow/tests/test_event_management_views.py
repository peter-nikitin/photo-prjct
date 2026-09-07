from datetime import UTC, datetime, timedelta

from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ingestion.models import ImportBatch, ImportScope, UploadBatch

from picflow import event_management_views
from picflow.models import EventFolder, Photo
from picflow.tests.event_management_helpers import (
    captured_at,
    event,
    private_photo,
    user_with_permissions,
)

ADMIN_PERMISSIONS = ("picflow.view_event", "picflow.view_photo")
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        "picflow/event_management.html": (
                            "{{ event.name }}{% for f in folders %}{{ f.name }}{% endfor %}"
                            "{% for p in photo_page %}{{ p.id }} {{ p.thumbnail_url }} "
                            "{{ p.original_url }}{% endfor %}"
                            "{% for b in batch_page %}{{ b.id }} "
                            "{{ b.confirmed_count }}{% endfor %}"
                        ),
                        "picflow/_event_photo_results.html": (
                            "{% if filters_valid %}{% for p in photo_page %}{{ p.id }} "
                            "{% endfor %}{% else %}invalid{% endif %}"
                        ),
                    },
                )
            ],
        },
    }
]


@override_settings(
    ROOT_URLCONF="picflow.tests.event_management_urlconf",
    TEMPLATES=TEMPLATES,
    PHOTO_UPLOAD_ENABLED=True,
)
class EventManagementViewTests(TestCase):
    def setUp(self):
        self.event = event()
        self.admin = user_with_permissions("admin", staff=True, permissions=ADMIN_PERMISSIONS)
        self.uploader = user_with_permissions("uploader", permissions=("ingestion.upload_photos",))
        self.url = reverse("event_management", args=[self.event.pk])

    def test_admin_inspection_is_independent_of_upload_permission_and_switch(self):
        hidden = private_photo(self.event, self.uploader, is_hidden=True)
        self.client.force_login(self.admin)
        for enabled in (True, False):
            with self.subTest(enabled=enabled), self.settings(PHOTO_UPLOAD_ENABLED=enabled):
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context_data["can_inspect"])
                self.assertFalse(response.context_data["can_upload"])
                self.assertNotIn("batch_page", response.context_data)
                self.assertEqual(response.context_data["processing_summary"]["total"], 1)
                self.assertEqual(response.context_data["photo_page"][0].id, hidden.pk)
                self.assertIsNone(response.context_data["photo_page"][0].thumbnail_url)
                self.assertContains(response, hidden.pk)
                self.assertNotContains(response, hidden.original_key)
                self.assertIn("private", response["Cache-Control"])
                self.assertIn("no-store", response["Cache-Control"])

    def test_uploader_only_gets_own_event_batches_and_empty_folder_targets(self):
        own = UploadBatch.objects.create(
            event=self.event, uploader=self.uploader, expected_item_count=1
        )
        foreign = UploadBatch.objects.create(
            event=self.event, uploader=self.admin, expected_item_count=1
        )
        other_event = UploadBatch.objects.create(
            event=event("Elsewhere"), uploader=self.uploader, expected_item_count=1
        )
        hidden = private_photo(self.event, self.admin, is_hidden=True)
        EventFolder.objects.create(event=self.event, name="Empty target")
        self.client.force_login(self.uploader)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["can_upload"])
        self.assertFalse(response.context_data["can_inspect"])
        for key in ("photo_page", "processing_summary"):
            self.assertNotIn(key, response.context_data)
        self.assertEqual(response.context_data["batch_page"].paginator.count, 1)
        self.assertContains(response, str(own.pk))
        self.assertContains(response, "Empty target")
        for secret in (str(foreign.pk), str(other_event.pk), hidden.pk, hidden.original_key):
            self.assertNotContains(response, secret)
        with self.settings(PHOTO_UPLOAD_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_partial_permissions_staff_only_and_nonstaff_admin_are_denied(self):
        roles = [
            (True, ()),
            (True, ADMIN_PERMISSIONS[:1]),
            (True, ADMIN_PERMISSIONS[1:]),
            (False, ADMIN_PERMISSIONS),
        ]
        for index, (staff, permissions) in enumerate(roles):
            with self.subTest(role=index):
                user = user_with_permissions(
                    f"partial-{index}", staff=staff, permissions=permissions
                )
                self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 403)
                self.assertIn("no-store", response["Cache-Control"])

    def test_inactive_user_denied_and_anonymous_redirects_to_existing_login(self):
        self.admin.is_active = False
        request = RequestFactory().get(self.url)
        request.user = self.admin
        self.assertEqual(
            event_management_views.event_management(request, self.event.pk).status_code, 403
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('photographer_login')}?next={self.url}")

    def test_admin_page_contains_all_photos_in_stable_capture_order_with_100_limit(self):
        photos = [
            Photo(
                id=f"legacy-{i:03d}", event=self.event, src=f"photos/secret-{i}.jpg", is_hidden=True
            )
            for i in range(101)
        ]
        Photo.objects.bulk_create(photos)
        capture_time = timezone.now() - timedelta(days=1)
        for photo in photos[-2:]:
            captured_at(photo, capture_time)
        self.client.force_login(self.admin)
        first = self.client.get(self.url)
        page = first.context_data["photo_page"]
        self.assertEqual(page.paginator.count, 101)
        self.assertEqual(len(page), 100)
        self.assertEqual([p.id for p in page][:3], ["legacy-099", "legacy-100", "legacy-000"])
        self.assertTrue(all(p.processing["category"] == "not_required" for p in page))
        self.assertNotContains(first, "/media/photos/")
        self.assertNotIn("secret-", repr(page.object_list))
        second = self.client.get(self.url, {"page": 2})
        self.assertEqual([p.id for p in second.context_data["photo_page"]], ["legacy-098"])

    def test_filters_are_strict_for_initial_and_fragment_gets(self):
        folder = EventFolder.objects.create(event=self.event, name="Finish")
        matching = private_photo(self.event, self.uploader, folder=folder, is_hidden=True)
        private_photo(self.event, self.uploader, folder=folder, is_hidden=False)
        self.client.force_login(self.admin)

        response = self.client.get(self.url, {"folder": folder.pk, "visibility": "hidden"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([photo.id for photo in response.context_data["photo_page"]], [matching.pk])
        self.assertTrue(response.context_data["filters_valid"])
        self.assertIn(f"folder={folder.pk}", response.context_data["canonical_query"])

        invalid = self.client.get(self.url, {"folder": 999999})
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(invalid.context_data["filters_valid"])
        self.assertEqual(invalid.context_data["photo_page"].paginator.count, 0)

        fragment = self.client.get(
            reverse("event_management_results", args=[self.event.pk]),
            {"page": ["1", "2"]},
        )
        self.assertEqual(fragment.status_code, 422)
        self.assertContains(fragment, "invalid", status_code=422)

    def test_page_overflow_clamps_to_last_page_and_returns_canonical_url(self):
        Photo.objects.bulk_create(
            [
                Photo(
                    id=f"overflow-{index:03d}",
                    event=self.event,
                    src=f"photos/overflow-{index:03d}.jpg",
                )
                for index in range(101)
            ]
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("event_management_results", args=[self.event.pk]), {"page": 999}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data["photo_page"].number, 2)
        self.assertTrue(response["X-Event-Photo-Canonical-Url"].endswith("?page=2"))

    def test_admin_with_upload_permission_receives_both_capabilities(self):
        user = user_with_permissions(
            "both", staff=True, permissions=(*ADMIN_PERMISSIONS, "ingestion.upload_photos")
        )
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertTrue(response.context_data["can_inspect"])
        self.assertTrue(response.context_data["can_upload"])
        self.assertIn("batch_page", response.context_data)
        self.assertIn("photo_page", response.context_data)

    def test_missing_event_and_non_get_are_private_errors(self):
        self.client.force_login(self.admin)
        for response in (
            self.client.get(reverse("event_management", args=[999999])),
            self.client.post(self.url),
        ):
            self.assertIn(response.status_code, (404, 405))
            self.assertIn("no-store", response["Cache-Control"])


@override_settings(
    ROOT_URLCONF="config.urls",
    PHOTO_UPLOAD_ENABLED=True,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class WorkspaceIntegrationTests(TestCase):
    def setUp(self):
        self.event = event("Workspace")
        self.user = user_with_permissions(
            "workspace-owner", permissions=("ingestion.upload_photos",)
        )
        self.client.force_login(self.user)

    def test_chooser_links_to_fixed_event_without_mounting_either_uploader(self):
        response = self.client.get(reverse("upload_page"))
        self.assertContains(response, f"/manage/events/{self.event.pk}/photos/")
        for forbidden in (
            "data-upload-root",
            "data-import-root",
            'id="upload-event"',
            'type="file"',
        ):
            self.assertNotContains(response, forbidden)

    def test_workspace_mounts_only_owned_event_upload_context_and_real_fragments(self):
        target = EventFolder.objects.create(event=self.event, name="Finish target")
        other = event("Other workspace")
        EventFolder.objects.create(event=other, name="Foreign target")
        owned = UploadBatch.objects.create(
            event=self.event, uploader=self.user, expected_item_count=1
        )
        foreign_event = UploadBatch.objects.create(
            event=other, uploader=self.user, expected_item_count=1
        )
        response = self.client.get(f"/manage/events/{self.event.pk}/photos/")
        self.assertEqual(response.status_code, 200)
        for template in (
            "picflow/event_management.html",
            "ingestion/_upload_workspace.html",
            "ingestion/_yandex_disk_import.html",
        ):
            self.assertTemplateUsed(response, template)
        self.assertContains(response, f'data-event-id="{self.event.pk}"')
        self.assertContains(response, f'data-folder-id="{target.pk}"')
        self.assertContains(response, f'data-batch-status-id="{owned.pk}"')
        self.assertNotContains(response, str(foreign_event.pk))
        self.assertNotContains(response, "Foreign target")
        self.assertNotContains(response, 'id="upload-event"')
        self.assertNotContains(response, "data-photo-status-id=")
        self.assertNotContains(response, "data-event-photo-summary-total")
        self.assertContains(response, 'data-import-history-enabled="true"')
        self.assertContains(response, 'data-import-enabled="false"')
        self.assertContains(response, "data-status-url=")

    def test_workspace_mounts_status_first_and_keeps_resume_input_outside_history_fragment(self):
        response = self.client.get(f"/manage/events/{self.event.pk}/photos/")
        html = response.content.decode()

        self.assertContains(response, "data-batch-history-url=")
        self.assertContains(response, "data-batch-history-fragment")
        self.assertContains(response, 'id="resume-upload-files"')
        fragment_start = html.index("data-batch-history-fragment")
        fragment_end = html.index("</div>", fragment_start)
        resume_input = html.index('id="resume-upload-files"')
        self.assertGreater(resume_input, fragment_end)
        self.assertLess(html.index("event-photo-status.js"), html.index("upload-coordinator.js"))
        self.assertLess(html.index("event-photo-status.js"), html.index("import-coordinator.js"))

    def test_history_fragment_pins_requested_owned_event_batch_and_stays_bounded(self):
        own = [
            UploadBatch.objects.create(
                event=self.event,
                uploader=self.user,
                expected_item_count=1,
            )
            for _ in range(22)
        ]
        foreign_owner = user_with_permissions(
            "history-foreign",
            permissions=("ingestion.upload_photos",),
        )
        foreign = UploadBatch.objects.create(
            event=self.event,
            uploader=foreign_owner,
            expected_item_count=1,
        )
        other_event = UploadBatch.objects.create(
            event=event("History elsewhere"),
            uploader=self.user,
            expected_item_count=1,
        )
        url = reverse("event_management_batch_history", args=[self.event.pk])

        response = self.client.get(url, {"batch_id": own[0].pk})

        self.assertEqual(response.status_code, 200)
        rows = list(response.context_data["batch_page"])
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0].id, own[0].pk)
        content = response.content.decode()
        self.assertIn("data-batch-history-fragment", content)
        self.assertNotIn('id="resume-upload-files"', content)
        self.assertNotIn(str(foreign.pk), content)
        self.assertNotIn(str(other_event.pk), content)

        self.assertEqual(self.client.get(url, {"batch_id": foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {"batch_id": "invalid"}).status_code, 400)

    def test_admin_without_upload_can_use_chooser_and_inspect_private_cards_when_gate_off(self):
        admin = user_with_permissions("workspace-admin", staff=True, permissions=ADMIN_PERMISSIONS)
        hidden = private_photo(self.event, self.user, is_hidden=True)
        self.client.force_login(admin)
        with self.settings(PHOTO_UPLOAD_ENABLED=False):
            chooser = self.client.get(reverse("upload_page"))
            response = self.client.get(f"/manage/events/{self.event.pk}/photos/")
        self.assertEqual(chooser.status_code, 200)
        self.assertContains(response, hidden.pk)
        self.assertContains(response, "data-photo-status-id=")
        self.assertContains(response, "Превью пока нет")
        self.assertContains(response, "Скрыто")
        self.assertNotContains(response, hidden.original_key)
        for forbidden in (
            "data-upload-root",
            "data-import-root",
            "data-resume-batch",
            "data-photo-action",
        ):
            self.assertNotContains(response, forbidden)

    def test_time_filter_names_event_timezone_or_says_it_is_unspecified(self):
        admin = user_with_permissions("timezone-admin", staff=True, permissions=ADMIN_PERMISSIONS)
        photo = private_photo(self.event, admin)
        captured_at(photo, datetime(2026, 9, 7, 12, 34, tzinfo=UTC))
        self.client.force_login(admin)
        unspecified = self.client.get(f"/manage/events/{self.event.pk}/photos/")
        self.assertContains(unspecified, "Часовой пояс мероприятия не указан")
        self.assertContains(unspecified, "07.09.2026 12:34 UTC")

        self.event.timezone_name = "Europe/Moscow"
        self.event.save(update_fields=["timezone_name"])
        configured = self.client.get(f"/manage/events/{self.event.pk}/photos/")
        self.assertContains(configured, "Часовой пояс: Europe/Moscow")
        self.assertContains(configured, "07.09.2026 15:34")
        self.assertNotContains(configured, "файлы в хранилище")

    @override_settings(YANDEX_METRIKA_COUNTER_ID=123456)
    def test_private_workspace_and_chooser_do_not_emit_analytics_session_recording(self):
        for url in (reverse("upload_page"), f"/manage/events/{self.event.pk}/photos/"):
            self.assertNotContains(self.client.get(url), "mc.yandex.ru")


@override_settings(
    ROOT_URLCONF="config.urls",
    PHOTO_UPLOAD_ENABLED=True,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class EventManagementMutationViewTests(TestCase):
    def setUp(self):
        self.event = event("Managed workspace")
        self.other_event = event("Foreign workspace")
        self.uploader = user_with_permissions("mutation-uploader")
        self.admin = user_with_permissions(
            "mutation-admin",
            staff=True,
            permissions=(
                *ADMIN_PERMISSIONS,
                "picflow.change_photo",
                "picflow.add_eventfolder",
                "picflow.change_eventfolder",
                "picflow.delete_eventfolder",
            ),
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        self.url = reverse("event_management", args=[self.event.pk])
        self.client.get(self.url)
        self.csrf = self.client.cookies["csrftoken"].value

    def post(self, name, data, *, event=None, csrf=True):
        return self.client.post(
            reverse(name, args=[(event or self.event).pk]),
            data,
            HTTP_X_CSRFTOKEN=self.csrf if csrf else "",
        )

    def test_controls_match_capabilities_and_mutations_require_csrf(self):
        EventFolder.objects.create(event=self.event, name="Existing")
        response = self.client.get(self.url)
        for control in (
            "data-folder-create-form",
            "data-folder-rename-form",
            "data-folder-delete-form",
            "data-photo-action-form",
        ):
            self.assertContains(response, control)
        denied = self.post("event_management_folder_create", {"name": "Start"}, csrf=False)
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(EventFolder.objects.filter(event=self.event, name="Start").exists())

        read_only = user_with_permissions("read-only", staff=True, permissions=ADMIN_PERMISSIONS)
        self.client.force_login(read_only)
        read_only_page = self.client.get(self.url)
        for control in (
            "data-folder-create-form",
            "data-folder-rename-form",
            "data-folder-delete-form",
            "data-photo-action-form",
        ):
            self.assertNotContains(read_only_page, control)
        self.assertEqual(
            self.client.post(
                reverse("event_management_folder_create", args=[self.event.pk]),
                {"name": "Forbidden"},
                HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
            ).status_code,
            403,
        )

    def test_folder_create_rename_delete_and_import_protection(self):
        created = self.post("event_management_folder_create", {"name": "  Start  "})
        self.assertEqual(created.status_code, 200)
        folder = EventFolder.objects.get(event=self.event)
        self.assertEqual(folder.name, "Start")
        self.assertEqual(created.json()["folders"], [{"id": folder.pk, "name": "Start"}])

        renamed = self.post(
            "event_management_folder_rename", {"folder": folder.pk, "name": "Finish"}
        )
        self.assertEqual(renamed.status_code, 200)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Finish")

        scope = ImportScope.objects.create(
            owner=self.admin,
            event=self.event,
            folder=folder,
            canonical_source_key="public:folder",
        )
        ImportBatch.objects.create(
            owner=self.admin,
            event=self.event,
            folder=folder,
            scope=scope,
            submitted_source_key="public:folder",
            submission_key="task-4-protect",
        )
        protected = self.post("event_management_folder_delete", {"folder": folder.pk})
        self.assertEqual(protected.status_code, 422)
        self.assertIn("используется", str(protected.json()))
        self.assertTrue(EventFolder.objects.filter(pk=folder.pk).exists())

        empty = EventFolder.objects.create(event=self.event, name="Empty")
        deleted = self.post("event_management_folder_delete", {"folder": empty.pk})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(EventFolder.objects.filter(pk=empty.pk).exists())

    def test_explicit_and_all_filtered_actions_report_actual_changes(self):
        folder = EventFolder.objects.create(event=self.event, name="Start")
        selected = private_photo(self.event, self.uploader, folder=folder)
        unselected = private_photo(self.event, self.uploader)

        explicit = self.post(
            "event_management_action",
            {"selection_mode": "explicit", "photo_id": selected.pk, "action": "hide"},
        )
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(explicit.json()["changed_count"], 1)
        unselected.refresh_from_db()
        self.assertFalse(unselected.is_hidden)
        repeated = self.post(
            "event_management_action",
            {"selection_mode": "explicit", "photo_id": selected.pk, "action": "hide"},
        )
        self.assertEqual(repeated.json()["changed_count"], 0)

        late = private_photo(self.event, self.uploader, folder=folder)
        all_filtered = self.post(
            "event_management_action",
            {
                "selection_mode": "all_filtered",
                "folder": folder.pk,
                "visibility": "visible",
                "action": "hide",
            },
        )
        self.assertEqual(all_filtered.status_code, 200)
        self.assertEqual(all_filtered.json()["changed_count"], 1)
        late.refresh_from_db()
        self.assertTrue(late.is_hidden)

    def test_cross_event_explicit_action_is_atomic(self):
        local = private_photo(self.event, self.uploader)
        foreign = private_photo(self.other_event, self.uploader)
        response = self.post(
            "event_management_action",
            {
                "selection_mode": "explicit",
                "photo_id": [local.pk, foreign.pk],
                "action": "hide",
            },
        )
        self.assertEqual(response.status_code, 422)
        local.refresh_from_db()
        foreign.refresh_from_db()
        self.assertFalse(local.is_hidden)
        self.assertFalse(foreign.is_hidden)
