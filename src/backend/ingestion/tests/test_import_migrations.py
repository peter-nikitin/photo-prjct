from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PhotoImportMigrationTests(TransactionTestCase):
    migrate_from = [("ingestion", "0003_uploaditem_folder")]
    migrate_to = [("ingestion", "0004_photo_import")]

    def test_upgrade_preserves_browser_upload_rows_and_adds_import_tables(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        Event = old_apps.get_model("picflow", "Event")
        UploadBatch = old_apps.get_model("ingestion", "UploadBatch")
        UploadItem = old_apps.get_model("ingestion", "UploadItem")

        owner = User.objects.create(username="migration-import-owner")
        event = Event.objects.create(
            name="Import migration",
            slug="import-migration",
            start_date="2026-09-07",
            end_date="2026-09-07",
            city="Moscow",
            access_type="free",
            publication_status="unavailable",
        )
        batch = UploadBatch.objects.create(
            event=event,
            uploader=owner,
            expected_item_count=1,
        )
        item = UploadItem.objects.create(
            batch=batch,
            client_item_id="00000000-0000-4000-8000-000000000001",
            original_filename="existing.jpg",
            declared_content_type="image/jpeg",
            expected_size=123,
            incoming_key="incoming/00000000-0000-4000-8000-000000000001/00000000-0000-4000-8000-000000000002",
            final_key="originals/00000000000040008000000000000002",
        )

        forward = MigrationExecutor(connection)
        forward.migrate(self.migrate_to)
        migrated_apps = forward.loader.project_state(self.migrate_to).apps
        MigratedBatch = migrated_apps.get_model("ingestion", "UploadBatch")
        MigratedItem = migrated_apps.get_model("ingestion", "UploadItem")

        self.assertTrue(MigratedBatch.objects.filter(pk=batch.pk).exists())
        self.assertTrue(MigratedItem.objects.filter(pk=item.pk).exists())
        for model_name in (
            "importscope",
            "importbatch",
            "importmanifestpage",
            "importitem",
            "importattempt",
            "importedcontent",
        ):
            self.assertIn(model_name, migrated_apps.all_models["ingestion"])

        restorer = MigrationExecutor(connection)
        restorer.migrate(restorer.loader.graph.leaf_nodes())
