from datetime import date
from io import StringIO

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PhotoMigrationTests(TransactionTestCase):
    reset_sequences = True

    def test_legacy_rows_survive_and_private_shape_is_enforced(self) -> None:
        executor = MigrationExecutor(connection)
        leaf_nodes = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("picflow", "0002_event_catalog")])
            old_apps = executor.loader.project_state([("picflow", "0002_event_catalog")]).apps
            Event = old_apps.get_model("picflow", "Event")
            Photo = old_apps.get_model("picflow", "Photo")
            event = Event.objects.create(
                id=1000,
                name="Migration Event",
                slug="migration-event",
                start_date=date.today(),
                end_date=date.today(),
                city="Moscow",
            )
            Photo.objects.create(id="LEGACY", event=event, src="photos/legacy.jpg")

            executor = MigrationExecutor(connection)
            executor.migrate([("picflow", "0005_validate_photo_private_original_constraints")])
            apps = executor.loader.project_state(
                [("picflow", "0005_validate_photo_private_original_constraints")]
            ).apps
            MigratedPhoto = apps.get_model("picflow", "Photo")
            migrated_photo = MigratedPhoto.objects.get(pk="LEGACY")
            self.assertEqual(migrated_photo.pk, "LEGACY")
            self.assertEqual(migrated_photo.event_id, event.pk)
            self.assertEqual(migrated_photo.src.name, "photos/legacy.jpg")
            self.assertIsNone(migrated_photo.original_key)

            User = apps.get_model("auth", "User")
            uploader = User.objects.create(username="migration-photographer")
            private_values = {
                "event_id": event.pk,
                "src": "",
                "uploaded_by_id": uploader.pk,
                "original_filename": "race.jpg",
                "original_size": 1024,
                "original_content_type": "image/jpeg",
                "uploaded_at": "2026-07-13T12:00:00+03:00",
            }
            MigratedPhoto.objects.create(
                id="PRIVATE-1", original_key="originals/private-1", **private_values
            )
            with self.assertRaises(IntegrityError), transaction.atomic():
                MigratedPhoto.objects.create(
                    id="INCOMPLETE",
                    event_id=event.pk,
                    src="",
                    original_key="originals/incomplete",
                )
            with self.assertRaises(IntegrityError), transaction.atomic():
                MigratedPhoto.objects.create(
                    id="PRIVATE-2", original_key="originals/private-1", **private_values
                )
        finally:
            MigrationExecutor(connection).migrate(leaf_nodes)

    def test_sql_uses_safe_named_operations(self) -> None:
        from django.core.management import call_command

        outputs = {}
        for app_label, migration in (
            ("picflow", "0003"),
            ("picflow", "0004"),
            ("picflow", "0005"),
            ("ingestion", "0001"),
        ):
            stdout = StringIO()
            call_command("sqlmigrate", app_label, migration, stdout=stdout)
            outputs[(app_label, migration)] = stdout.getvalue()

        expand = outputs[("picflow", "0003")]
        constraints = outputs[("picflow", "0004")]
        validation = outputs[("picflow", "0005")]
        self.assertIn('ADD COLUMN "original_key" varchar(255) NULL', expand)
        self.assertIn("SET LOCAL lock_timeout = '2s'", expand)
        self.assertNotIn("DROP COLUMN", expand)
        self.assertIn("CREATE INDEX CONCURRENTLY picflow_photo_uploaded_by_idx", constraints)
        self.assertIn(
            "CREATE UNIQUE INDEX CONCURRENTLY picflow_photo_original_key_uniq", constraints
        )
        self.assertNotIn("WHERE", constraints.split("CREATE UNIQUE INDEX", 1)[1].split(";", 1)[0])
        self.assertIn(
            "ADD CONSTRAINT picflow_photo_original_key_uniq UNIQUE USING INDEX "
            "picflow_photo_original_key_uniq",
            constraints,
        )
        self.assertIn("picflow_photo_uploaded_by_fk", constraints)
        self.assertIn("NOT VALID", constraints)
        self.assertIn("VALIDATE CONSTRAINT picflow_photo_uploaded_by_fk", validation)
        self.assertIn("VALIDATE CONSTRAINT picflow_photo_legacy_or_private_chk", validation)
        self.assertNotIn("lock_timeout", validation)
