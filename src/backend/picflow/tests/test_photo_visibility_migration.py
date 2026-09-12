from datetime import date

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PhotoVisibilityMigrationTests(TransactionTestCase):
    migrate_from = ("picflow", "0013_event_photo_price")
    migrate_to = ("picflow", "0014_photo_is_hidden")

    def tearDown(self) -> None:
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_existing_photo_relationships_and_media_identity_survive_as_visible(self) -> None:
        executor = MigrationExecutor(connection)
        self.assertIn(self.migrate_to, executor.loader.graph.leaf_nodes("picflow"))
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Event = old_apps.get_model("picflow", "Event")
        EventFolder = old_apps.get_model("picflow", "EventFolder")
        Photo = old_apps.get_model("picflow", "Photo")
        user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".", 1)
        User = old_apps.get_model(user_app_label, user_model_name)

        uploader = User.objects.create(username="visibility-migration-owner")
        event = Event.objects.create(
            name="Visibility migration event",
            slug="visibility-migration-event",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 7),
            city="Moscow",
            timezone_name="Europe/Moscow",
            publication_status="published",
            access_type="paid",
            price_per_photo_kopecks=30000,
        )
        folder = EventFolder.objects.create(event=event, name="Finish")
        photo = Photo.objects.create(
            id="visibility-migration-photo",
            event=event,
            folder=folder,
            src="",
            uploaded_by=uploader,
            original_key="originals/visibility-migration-photo.jpg",
            original_filename="camera-original.jpg",
            original_size=12345,
            original_content_type="image/jpeg",
            uploaded_at="2026-09-07T09:00:00+03:00",
            processing_generation="preview_first_watermarked_v1",
            gallery_media_policy="watermarked_preview_required",
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        MigratedPhoto = apps.get_model("picflow", "Photo")
        migrated = MigratedPhoto.objects.get(pk=photo.pk)

        self.assertFalse(migrated.is_hidden)
        self.assertEqual(migrated.event_id, event.pk)
        self.assertEqual(migrated.folder_id, folder.pk)
        self.assertEqual(migrated.uploaded_by_id, uploader.pk)
        self.assertEqual(migrated.original_key, "originals/visibility-migration-photo.jpg")
        self.assertEqual(migrated.original_filename, "camera-original.jpg")
        self.assertEqual(migrated.original_size, 12345)
        self.assertEqual(migrated.original_content_type, "image/jpeg")
        self.assertEqual(migrated.processing_generation, "preview_first_watermarked_v1")
        self.assertEqual(migrated.gallery_media_policy, "watermarked_preview_required")
