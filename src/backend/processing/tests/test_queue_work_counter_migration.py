from datetime import date

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.migration]


def test_queue_work_upgrade_seeds_unavailable_counts_without_reading_existing_jobs():
    previous = [("processing", "0022_personappearancedetection")]
    target = [("processing", "0023_queue_work_counters")]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        event = apps.get_model("picflow", "Event").objects.create(
            name="Existing queue",
            slug="existing-queue",
            start_date=date.today(),
            end_date=date.today(),
        )
        photo = apps.get_model("picflow", "Photo").objects.create(
            id="queued-photo", event=event, src="original.jpg"
        )
        processor = {
            "processor_type": "person_appearance",
            "contract_version": 2,
            "processor_version": 1,
            "configuration_hash": "a" * 64,
        }
        run = apps.get_model("processing", "EventProcessingRun").objects.create(
            event=event, **processor
        )
        job = apps.get_model("processing", "ProcessingJob").objects.create(
            event=event, run=run, photo=photo, status="processing", **processor
        )

        executor = MigrationExecutor(connection)
        with CaptureQueriesContext(connection) as queries:
            executor.migrate(target)
        business_tables = (
            "processing_processingjob",
            "selfie_search_facesearchtask",
            "selfie_search_selfiesearchjob",
            "ingestion_importbatch",
            "commerce_emaildelivery",
            "commerce_paymentattempt",
            "picflow_photoarchivejob",
            '"picflow_photo"',
        )
        assert not any(table in query["sql"] for query in queries for table in business_tables)

        current = executor.loader.project_state(target).apps
        assert set(
            current.get_model("processing", "QueueWorkCounter").objects.values_list(
                "queue", "processor", "waiting", "processing", "retry_wait", "paused", "initialized"
            )
        ) == {
            (queue, "", 0, 0, 0, 0, False)
            for queue in (
                "gallery-search",
                "selfie",
                "selfie_face_search",
                "photo",
                "import",
                "commerce",
                "photo_archive",
            )
        }
        assert (
            current.get_model("processing", "ProcessingJob").objects.get(pk=job.pk).status
            == "processing"
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)
