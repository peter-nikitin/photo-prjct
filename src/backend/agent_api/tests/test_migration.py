from datetime import date

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from ingestion.models import UploadBatch
from picflow.models import Event


@pytest.mark.migration
def test_agent_tables_can_be_removed_and_restored_without_touching_uploads(
    transactional_db,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="photographer")
    event = Event.objects.create(
        name="Race",
        slug="race",
        start_date=date.today(),
        end_date=date.today(),
    )
    batch = UploadBatch.objects.create(uploader=user, event=event, expected_item_count=1)
    original = ("agent_api", "0001_initial")
    tables = {"agent_api_agenttoken", "agent_api_agentupload"}
    try:
        MigrationExecutor(connection).migrate([("agent_api", None)])
        assert tables.isdisjoint(connection.introspection.table_names())
        assert UploadBatch.objects.filter(pk=batch.pk, uploader=user, event=event).exists()
    finally:
        MigrationExecutor(connection).migrate([original])
    assert tables.issubset(connection.introspection.table_names())
    assert UploadBatch.objects.filter(pk=batch.pk, uploader=user, event=event).exists()
