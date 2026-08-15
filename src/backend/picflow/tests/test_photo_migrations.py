from datetime import date
from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.writer import MigrationWriter
from django.test import TransactionTestCase


class PhotoMigrationTests(TransactionTestCase):
    reset_sequences = True

    def _restore_current_migration_leaf(self) -> None:
        restorer = MigrationExecutor(connection)
        restorer.migrate(restorer.loader.graph.leaf_nodes())

    def tearDown(self) -> None:
        try:
            self._restore_current_migration_leaf()
        finally:
            super().tearDown()

    def test_teardown_restores_the_current_migration_leaf(self) -> None:
        MigrationExecutor(connection).migrate([("picflow", "0007_event_timezone")])
        try:
            self.tearDown()
            with connection.cursor() as cursor:
                column_names = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        cursor, "picflow_photo"
                    )
                }

            self.assertIn("capture_time", column_names)
            self.assertIn("capture_time_source_attempt_id", column_names)
        finally:
            self._restore_current_migration_leaf()

    def test_legacy_rows_survive_and_private_shape_is_enforced(self) -> None:
        executor = MigrationExecutor(connection)
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
            executor.migrate([("picflow", "0006_photo_processing_policy")])
            apps = executor.loader.project_state([("picflow", "0006_photo_processing_policy")]).apps
            MigratedPhoto = apps.get_model("picflow", "Photo")
            migrated_photo = MigratedPhoto.objects.get(pk="LEGACY")
            self.assertEqual(migrated_photo.pk, "LEGACY")
            self.assertEqual(migrated_photo.event_id, event.pk)
            self.assertEqual(migrated_photo.src.name, "photos/legacy.jpg")
            self.assertIsNone(migrated_photo.original_key)
            self.assertEqual(migrated_photo.processing_generation, "legacy_original_v1")
            self.assertEqual(migrated_photo.gallery_media_policy, "legacy_original_allowed")

            user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".", 1)
            User = apps.get_model(user_app_label, user_model_name)
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
            self._restore_current_migration_leaf()

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

        backwards_stdout = StringIO()
        call_command(
            "sqlmigrate",
            "picflow",
            "0003",
            backwards=True,
            stdout=backwards_stdout,
        )
        backwards_expand = backwards_stdout.getvalue()
        backwards_constraints_stdout = StringIO()
        call_command(
            "sqlmigrate",
            "picflow",
            "0004",
            backwards=True,
            stdout=backwards_constraints_stdout,
        )
        backwards_constraints = backwards_constraints_stdout.getvalue()

        expand = outputs[("picflow", "0003")]
        constraints = outputs[("picflow", "0004")]
        validation = outputs[("picflow", "0005")]
        self.assertIn('ADD COLUMN "original_key" varchar(255) NULL', expand)
        self.assertIn("SET LOCAL lock_timeout = '2s'", expand)
        self.assertLess(
            expand.index("SET LOCAL lock_timeout = '2s'"),
            expand.index("ADD COLUMN"),
        )
        self.assertNotIn("DROP COLUMN", expand)
        self.assertLess(
            backwards_expand.index("SET LOCAL lock_timeout = '2s'"),
            backwards_expand.index("DROP COLUMN"),
        )
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
        self.assertIn('REFERENCES "auth_user" ("id")', constraints)
        self.assertIn("NOT VALID", constraints)
        self.assertIn("VALIDATE CONSTRAINT picflow_photo_uploaded_by_fk", validation)
        self.assertIn("VALIDATE CONSTRAINT picflow_photo_legacy_or_private_chk", validation)
        self.assertNotIn("lock_timeout", validation)
        for constraint_name in (
            "picflow_photo_legacy_or_private_chk",
            "picflow_photo_uploaded_by_fk",
            "picflow_photo_original_key_uniq",
        ):
            self.assertIn(
                "BEGIN; SET LOCAL lock_timeout = '2s'; "
                f"ALTER TABLE picflow_photo DROP CONSTRAINT {constraint_name}; COMMIT;",
                backwards_constraints,
            )
        self.assertIn(
            "DROP INDEX CONCURRENTLY IF EXISTS picflow_photo_original_key_uniq",
            backwards_constraints,
        )
        self.assertIn(
            "DROP INDEX CONCURRENTLY IF EXISTS picflow_photo_uploaded_by_idx",
            backwards_constraints,
        )

    def test_uploaded_by_fk_uses_historical_user_table_and_primary_key(self) -> None:
        migration_module = import_module("picflow.migration_operations")
        operation = migration_module.AddNotValidUploadedByForeignKey()
        historical_user = SimpleNamespace(
            _meta=SimpleNamespace(
                db_table="accounts_member",
                pk=SimpleNamespace(column="member_uuid"),
            )
        )
        apps = Mock()
        apps.get_model.return_value = historical_user
        to_state = SimpleNamespace(apps=apps)
        schema_editor = Mock()
        schema_editor.quote_name.side_effect = lambda value: f'"{value}"'

        operation.database_forwards("picflow", schema_editor, Mock(), to_state)

        apps.get_model.assert_called_once_with(*settings.AUTH_USER_MODEL.split(".", 1))
        sql = schema_editor.execute.call_args.args[0]
        self.assertIn('REFERENCES "accounts_member" ("member_uuid")', sql)
        self.assertNotIn("auth_user", sql)

    def test_custom_operation_migration_serializes_to_valid_python(self) -> None:
        migration = MigrationLoader(connection).get_migration(
            "picflow", "0004_photo_private_original_constraints"
        )

        source = MigrationWriter(migration).as_string()

        compile(source, "0004_photo_private_original_constraints.py", "exec")
        self.assertIn("import picflow.migration_operations", source)
        self.assertNotIn("import picflow.migrations.0004_", source)

    def test_event_timezone_migration_sets_only_event_nine_to_moscow(self) -> None:
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([("picflow", "0006_photo_processing_policy")])
            old_apps = executor.loader.project_state(
                [("picflow", "0006_photo_processing_policy")]
            ).apps
            Event = old_apps.get_model("picflow", "Event")
            for event_id, name, city in (
                (9, "Cyclingrace Вечернее Садовое", "Moscow"),
                (10, "London event", "London"),
                (11, "Other Moscow event", "Moscow"),
            ):
                Event.objects.create(
                    id=event_id,
                    name=name,
                    slug=f"event-{event_id}",
                    start_date=date.today(),
                    end_date=date.today(),
                    city=city,
                )

            executor = MigrationExecutor(connection)
            executor.migrate([("picflow", "0007_event_timezone")])
            apps = executor.loader.project_state([("picflow", "0007_event_timezone")]).apps
            MigratedEvent = apps.get_model("picflow", "Event")

            self.assertEqual(MigratedEvent.objects.get(pk=9).timezone_name, "Europe/Moscow")
            self.assertIsNone(MigratedEvent.objects.get(pk=10).timezone_name)
            self.assertIsNone(MigratedEvent.objects.get(pk=11).timezone_name)
        finally:
            self._restore_current_migration_leaf()

    def test_capture_time_projection_migration_is_schema_only(self) -> None:
        """Catch a deployment-blocking data scan inside the nullable schema migration."""
        migration = MigrationLoader(connection).get_migration(
            "picflow", "0008_photo_capture_time_projection"
        )

        self.assertFalse(
            any(operation.__class__.__name__ == "RunPython" for operation in migration.operations)
        )
        self.assertEqual(
            migration.dependencies,
            [
                ("picflow", "0007_event_timezone"),
                ("processing", "0006_face_cluster_corpus"),
            ],
        )

    def test_event_folder_migration_leaves_existing_photos_unassigned(self) -> None:
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([("picflow", "0008_photo_capture_time_projection")])
            old_apps = executor.loader.project_state(
                [("picflow", "0008_photo_capture_time_projection")]
            ).apps
            Event = old_apps.get_model("picflow", "Event")
            Photo = old_apps.get_model("picflow", "Photo")
            event = Event.objects.create(
                id=1001,
                name="Folder migration event",
                slug="folder-migration-event",
                start_date=date.today(),
                end_date=date.today(),
                city="Moscow",
            )
            Photo.objects.create(id="PRE-FOLDER", event=event, src="photos/pre-folder.jpg")

            executor = MigrationExecutor(connection)
            executor.migrate([("picflow", "0009_eventfolder_photo_folder")])
            apps = executor.loader.project_state(
                [("picflow", "0009_eventfolder_photo_folder")]
            ).apps
            MigratedPhoto = apps.get_model("picflow", "Photo")

            self.assertIsNone(MigratedPhoto.objects.get(pk="PRE-FOLDER").folder_id)
        finally:
            self._restore_current_migration_leaf()
