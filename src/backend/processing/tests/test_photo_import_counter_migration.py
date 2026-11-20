from datetime import date

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.migration]


def test_import_counter_migration_seeds_rows_without_scanning_product_tables():
    previous = [("processing", "0020_merge_photo_metrics_and_pg_stat_statements")]
    target = [("processing", "0021_photo_import_counters")]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        event = apps.get_model("picflow", "Event").objects.create(
            name="Existing import",
            slug="existing-import",
            start_date=date.today(),
            end_date=date.today(),
        )
        photo = apps.get_model("picflow", "Photo").objects.create(
            id="existing-photo", event=event, src="original.jpg"
        )
        state = apps.get_model("processing", "PhotoProcessingState").objects.create(
            photo=photo, processor_type="face_embedding", status="queued"
        )

        executor = MigrationExecutor(connection)
        with CaptureQueriesContext(connection) as queries:
            executor.migrate(target)
        assert not any(
            table in query["sql"]
            for query in queries
            for table in ('"picflow_photo"', '"processing_photoprocessingstate"')
        )

        current = executor.loader.project_state(target).apps
        assert set(
            current.get_model("processing", "PhotoImportCounter").objects.values_list(
                "queue", "pending", "failed", "initialized"
            )
        ) == {("photo_import_face", 0, 0, False), ("photo_import_gallery", 0, 0, False)}
        assert current.get_model("picflow", "Photo").objects.get(pk=photo.pk).src == photo.src
        assert (
            current.get_model("processing", "PhotoProcessingState").objects.get(pk=state.pk).status
            == state.status
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)
