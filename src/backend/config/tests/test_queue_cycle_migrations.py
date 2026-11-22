from datetime import date, timedelta

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.migration]


def test_observation_columns_leave_existing_cycles_and_business_timestamps_unchanged():
    previous = [
        ("commerce", "0010_payment_initiation_started_at"),
        ("ingestion", "0004_photo_import"),
        ("picflow", "0019_photo_archive_job"),
    ]
    current = [
        ("commerce", "0011_queue_cycle_observations"),
        ("ingestion", "0005_queue_cycle_observations"),
        ("picflow", "0020_queue_cycle_observations"),
    ]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        old = executor.loader.project_state(previous).apps
        user = old.get_model("auth", "User").objects.create(username="existing-owner")
        event = old.get_model("picflow", "Event").objects.create(
            name="Retained event",
            slug="retained-event",
            start_date=date.today(),
            end_date=date.today(),
        )
        batch = old.get_model("ingestion", "ImportBatch").objects.create(
            owner=user,
            event=event,
            submission_key="retained",
            submitted_source_key="source",
            status="paused",
        )
        archive = old.get_model("picflow", "PhotoArchiveJob").objects.create(
            request_key="a" * 64,
            filename="photos.zip",
            entries=[],
            context="free_result",
            page=1,
            status="queued",
        )
        photo = old.get_model("picflow", "Photo").objects.create(
            id="upgrade-photo", event=event, src="photo.jpg"
        )
        with transaction.atomic():
            order = old.get_model("commerce", "Order").objects.create(
                event=event,
                checkout_email="photo@example.com",
                delivery_email="photo@example.com",
                total_kopecks=400,
            )
            old.get_model("commerce", "OrderItem").objects.create(
                order=order,
                photo=photo,
                photo_public_id=photo.pk,
                unit_price_kopecks=400,
                line_total_kopecks=400,
            )
            due_at = timezone.now() + timedelta(minutes=2)
            payment = old.get_model("commerce", "PaymentAttempt").objects.create(
                order=order,
                adapter_key="tbank",
                idempotency_key="retained",
                amount_kopecks=400,
                reconciliation_next_attempt_at=due_at,
            )

        executor = MigrationExecutor(connection)
        executor.migrate(current)
        new = executor.loader.project_state(current).apps
        retained_archive = new.get_model("picflow", "PhotoArchiveJob").objects.get(pk=archive.pk)
        retained_import = new.get_model("ingestion", "ImportBatch").objects.get(pk=batch.pk)
        retained_payment = new.get_model("commerce", "PaymentAttempt").objects.get(pk=payment.pk)
        assert (retained_import.status, retained_import.queue_timings) == ("paused", None)
        assert retained_import.last_activity_at == batch.last_activity_at
        assert (retained_archive.status, retained_archive.queue_enqueued_at) == ("queued", None)
        assert retained_archive.public_id == archive.public_id
        assert retained_payment.reconciliation_next_attempt_at == due_at
        assert retained_payment.queue_enqueued_at is None
        assert retained_payment.queue_gateway_ms is None
    finally:
        MigrationExecutor(connection).migrate(leaves)
