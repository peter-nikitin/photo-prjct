from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, modify_settings, override_settings
from django.urls import reverse

from picflow.models import Event, EventFolder, Photo


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
                "timezone_name": "Europe/Moscow",
                "folders-TOTAL_FORMS": "1",
                "folders-INITIAL_FORMS": "0",
                "folders-MIN_NUM_FORMS": "0",
                "folders-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.published().get(slug="admin-run")
        self.assertEqual(event.timezone_name, "Europe/Moscow")

    def test_admin_exposes_timezone_field(self) -> None:
        response = self.client.get(reverse("admin:picflow_event_add"))

        self.assertContains(response, 'name="timezone_name"')

    def test_admin_exposes_russian_publication_labels_with_unavailable_selected(self) -> None:
        response = self.client.get(reverse("admin:picflow_event_add"))

        self.assertContains(response, '<option value="unavailable" selected>Недоступно</option>')
        self.assertContains(response, '<option value="draft">Черновик</option>')
        self.assertContains(response, '<option value="published">Опубликовано</option>')

    def test_photo_admin_does_not_expose_capture_time_projection_fields(self) -> None:
        response = self.client.get(reverse("admin:picflow_photo_add"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "field-capture_time")
        self.assertNotContains(response, 'name="capture_time_source_attempt"')

    def test_photo_admin_does_not_expose_folder_reassignment(self) -> None:
        response = self.client.get(reverse("admin:picflow_photo_add"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "field-folder")
        self.assertNotContains(response, 'name="folder"')

    def photo_change_data(self, photo: Photo, **overrides) -> dict[str, object]:
        values: dict[str, object] = {
            "id": photo.pk,
            "event": str(photo.event_id),
            "processing_generation": photo.processing_generation,
            "gallery_media_policy": photo.gallery_media_policy,
            "_save": "Save",
        }
        values.update(overrides)
        return values

    def test_photo_admin_preserves_legitimate_create_behavior(self) -> None:
        event = Event.objects.create(
            name="Admin photo run",
            slug="admin-photo-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
        )

        response = self.client.post(
            reverse("admin:picflow_photo_add"),
            {
                "id": "admin-created-photo",
                "event": str(event.pk),
                "uploaded_by": str(self.user.pk),
                "original_key": "originals/admin-created-photo.jpg",
                "original_filename": "admin-created-photo.jpg",
                "original_size": "1234",
                "original_content_type": "image/jpeg",
                "uploaded_at_0": date.today().isoformat(),
                "uploaded_at_1": "12:00:00",
                "processing_generation": (Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1),
                "gallery_media_policy": (Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED),
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        photo = Photo.objects.get(pk="admin-created-photo")
        self.assertEqual(photo.event_id, event.pk)
        self.assertEqual(
            photo.processing_generation,
            Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
        )
        self.assertEqual(
            photo.gallery_media_policy,
            Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

    def test_photo_admin_rejects_folderless_watermarked_photo_reclassification_to_preview_pair(
        self,
    ) -> None:
        event = Event.objects.create(
            name="Paid run",
            slug="paid-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
        )
        photo = Photo.objects.create(
            id="watermarked-photo",
            event=event,
            src="photos/watermarked.jpg",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

        response = self.client.post(
            reverse("admin:picflow_photo_change", args=[photo.pk]),
            self.photo_change_data(
                photo,
                processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
                gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Processing generation cannot be changed after the photo has been created.",
            response.context["adminform"].form.errors["processing_generation"],
        )
        self.assertIn(
            "Gallery media policy cannot be changed after the photo has been created.",
            response.context["adminform"].form.errors["gallery_media_policy"],
        )
        photo.refresh_from_db()
        self.assertEqual(
            photo.processing_generation,
            Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
        )
        self.assertEqual(
            photo.gallery_media_policy,
            Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

    def test_photo_admin_rejects_folderless_watermarked_photo_reclassification_to_legacy_pair(
        self,
    ) -> None:
        event = Event.objects.create(
            name="Paid run",
            slug="paid-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
        )
        photo = Photo.objects.create(
            id="watermarked-photo",
            event=event,
            src="photos/watermarked.jpg",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

        response = self.client.post(
            reverse("admin:picflow_photo_change", args=[photo.pk]),
            self.photo_change_data(
                photo,
                processing_generation=Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1,
                gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Processing generation cannot be changed after the photo has been created.",
            response.context["adminform"].form.errors["processing_generation"],
        )
        self.assertIn(
            "Gallery media policy cannot be changed after the photo has been created.",
            response.context["adminform"].form.errors["gallery_media_policy"],
        )
        photo.refresh_from_db()
        self.assertEqual(
            photo.processing_generation,
            Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
        )
        self.assertEqual(
            photo.gallery_media_policy,
            Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

    def test_photo_admin_rejects_folderless_photo_move_and_keeps_original_event_frozen(
        self,
    ) -> None:
        event = Event.objects.create(
            name="Frozen paid run",
            slug="frozen-paid-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
        )
        other_event = Event.objects.create(
            name="Other run",
            slug="other-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        photo = Photo.objects.create(
            id="watermarked-photo",
            event=event,
            src="photos/watermarked.jpg",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )

        photo_response = self.client.post(
            reverse("admin:picflow_photo_change", args=[photo.pk]),
            self.photo_change_data(photo, event=str(other_event.pk)),
        )

        self.assertEqual(photo_response.status_code, 200)
        self.assertIn(
            "Event cannot be changed after the photo has been created.",
            photo_response.context["adminform"].form.errors["event"],
        )
        photo.refresh_from_db()
        self.assertEqual(photo.event_id, event.pk)

        event_response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(event, access_type=Event.AccessType.FREE),
        )

        self.assertEqual(event_response.status_code, 200)
        self.assertIn(
            "Access type cannot be changed after the event has photos.",
            event_response.context["adminform"].form.errors["access_type"],
        )
        event.refresh_from_db()
        self.assertEqual(event.access_type, Event.AccessType.PAID)

    def test_photo_admin_rejects_event_change_for_a_foldered_photo(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        other_event = Event.objects.create(
            name="Other Run",
            slug="other-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        folder = EventFolder.objects.create(event=event, name="Start")
        photo = Photo.objects.create(
            id="folder-photo", event=event, folder=folder, src="photos/folder.jpg"
        )

        response = self.client.post(
            reverse("admin:picflow_photo_change", args=[photo.pk]),
            self.photo_change_data(photo, event=str(other_event.pk)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Event cannot be changed after the photo has been created.",
            response.context["adminform"].form.errors["event"],
        )
        photo.refresh_from_db()
        self.assertEqual(photo.event_id, event.pk)
        self.assertEqual(photo.folder_id, folder.pk)

    def test_admin_rejects_published_event_without_timezone(self) -> None:
        response = self.client.post(
            reverse("admin:picflow_event_add"),
            {
                "name": "Published without timezone",
                "slug": "published-without-timezone",
                "start_date": date.today(),
                "end_date": date.today(),
                "city": "Moscow",
                "access_type": Event.AccessType.FREE,
                "publication_status": Event.PublicationStatus.PUBLISHED,
            },
        )

        self.assertContains(response, "Timezone is required for published events")
        self.assertFalse(Event.objects.filter(slug="published-without-timezone").exists())

    def test_admin_rejects_draft_event_without_timezone(self) -> None:
        response = self.client.post(
            reverse("admin:picflow_event_add"),
            {
                "name": "Draft without timezone",
                "slug": "draft-without-timezone",
                "start_date": date.today(),
                "end_date": date.today(),
                "city": "Moscow",
                "access_type": Event.AccessType.FREE,
                "publication_status": Event.PublicationStatus.DRAFT,
            },
        )

        self.assertContains(response, "Timezone is required for draft events")
        self.assertFalse(Event.objects.filter(slug="draft-without-timezone").exists())

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

    def event_change_data(self, event: Event, **overrides) -> dict[str, object]:
        values: dict[str, object] = {
            "name": event.name,
            "slug": event.slug,
            "start_date": event.start_date,
            "end_date": event.end_date,
            "city": event.city,
            "description": event.description,
            "access_type": event.access_type,
            "publication_status": event.publication_status,
            "timezone_name": event.timezone_name or "",
            "folders-TOTAL_FORMS": "0",
            "folders-INITIAL_FORMS": "0",
            "folders-MIN_NUM_FORMS": "0",
            "folders-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }
        values.update(overrides)
        return values

    def test_admin_changes_access_type_before_the_first_photo(self) -> None:
        event = Event.objects.create(
            name="Editable access",
            slug="editable-access",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(event, access_type=Event.AccessType.PAID),
        )

        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.access_type, Event.AccessType.PAID)

    def test_admin_rejects_access_type_change_after_the_first_photo(self) -> None:
        event = Event.objects.create(
            name="Frozen access",
            slug="frozen-access",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        Photo.objects.create(id="first-photo", event=event, src="photos/first.jpg")

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(event, access_type=Event.AccessType.PAID),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Access type cannot be changed after the event has photos.",
            response.context["adminform"].form.errors["access_type"],
        )
        event.refresh_from_db()
        self.assertEqual(event.access_type, Event.AccessType.FREE)

    def test_admin_keeps_unrelated_event_fields_editable_after_the_first_photo(self) -> None:
        event = Event.objects.create(
            name="Editable metadata",
            slug="editable-metadata",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        Photo.objects.create(id="metadata-photo", event=event, src="photos/metadata.jpg")

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(event, description="Updated description"),
        )

        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.description, "Updated description")

    def test_admin_adds_a_folder_on_the_event_change_page(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(
                event,
                **{
                    "folders-TOTAL_FORMS": "1",
                    "folders-INITIAL_FORMS": "0",
                    "folders-MIN_NUM_FORMS": "0",
                    "folders-MAX_NUM_FORMS": "1000",
                    "folders-0-name": "  Start  ",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(event.folders.values_list("name", flat=True)), ["Start"])

    def test_admin_renames_a_folder_on_the_event_change_page(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        folder = EventFolder.objects.create(event=event, name="Start")

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(
                event,
                **{
                    "folders-TOTAL_FORMS": "1",
                    "folders-INITIAL_FORMS": "1",
                    "folders-MIN_NUM_FORMS": "0",
                    "folders-MAX_NUM_FORMS": "1000",
                    "folders-0-id": str(folder.pk),
                    "folders-0-event": str(event.pk),
                    "folders-0-name": "Finish",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Finish")

    def test_admin_rejects_a_case_insensitive_duplicate_folder_name(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        folder = EventFolder.objects.create(event=event, name="Start")

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(
                event,
                **{
                    "folders-TOTAL_FORMS": "2",
                    "folders-INITIAL_FORMS": "1",
                    "folders-MIN_NUM_FORMS": "0",
                    "folders-MAX_NUM_FORMS": "1000",
                    "folders-0-id": str(folder.pk),
                    "folders-0-event": str(event.pk),
                    "folders-0-name": "Start",
                    "folders-1-name": "start",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(event.folders.values_list("name", flat=True)), ["Start"])

    def test_admin_rejects_two_new_normalized_duplicate_folder_names(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(
                event,
                **{
                    "folders-TOTAL_FORMS": "2",
                    "folders-INITIAL_FORMS": "0",
                    "folders-MIN_NUM_FORMS": "0",
                    "folders-MAX_NUM_FORMS": "1000",
                    "folders-0-name": "  Start  ",
                    "folders-1-name": "start",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Folder names must be unique within an event.")
        self.assertFalse(event.folders.exists())

    def test_admin_deletes_an_empty_folder_on_the_event_change_page(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        folder = EventFolder.objects.create(event=event, name="Start")

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(
                event,
                **{
                    "folders-TOTAL_FORMS": "1",
                    "folders-INITIAL_FORMS": "1",
                    "folders-MIN_NUM_FORMS": "0",
                    "folders-MAX_NUM_FORMS": "1000",
                    "folders-0-id": str(folder.pk),
                    "folders-0-event": str(event.pk),
                    "folders-0-name": "Start",
                    "folders-0-DELETE": "on",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(EventFolder.objects.filter(pk=folder.pk).exists())

    def test_admin_refuses_to_delete_a_folder_with_photos(self) -> None:
        event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        folder = EventFolder.objects.create(event=event, name="Start")
        Photo.objects.create(id="folder-photo", event=event, folder=folder, src="photos/folder.jpg")

        response = self.client.post(
            reverse("admin:picflow_event_change", args=[event.pk]),
            self.event_change_data(
                event,
                **{
                    "folders-TOTAL_FORMS": "1",
                    "folders-INITIAL_FORMS": "1",
                    "folders-MIN_NUM_FORMS": "0",
                    "folders-MAX_NUM_FORMS": "1000",
                    "folders-0-id": str(folder.pk),
                    "folders-0-event": str(event.pk),
                    "folders-0-name": "Start",
                    "folders-0-DELETE": "on",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "protected related objects")
        self.assertTrue(EventFolder.objects.filter(pk=folder.pk).exists())
