from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SelfieSearchMigrationTests(TransactionTestCase):
    migrate_from = [("processing", "0002_add_face_embedding_schema")]
    migrate_to = [("selfie_search", "0001_initial")]

    def test_schema_migrates_forward_and_back_without_errors(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        for model_name in (
            "selfiesearch",
            "selfiesearchcandidate",
            "selfiesearchjob",
            "selfiesearchattempt",
            "selfiesearchresult",
        ):
            self.assertIn(model_name, migrated_apps.all_models["selfie_search"])

        executor.migrate(self.migrate_from)
        reverted_apps = executor.loader.project_state(self.migrate_from).apps
        self.assertNotIn("selfie_search", reverted_apps.all_models)
