from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import psycopg
import pytest
from config.queue_telemetry_protocol import FACE_QUEUE, GALLERY_QUEUE
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone
from picflow.models import Event, Photo

from processing.photo_import_telemetry import schedule_original_accepted

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def counters():
    model = apps.get_model("processing", "PhotoImportCounter")
    for queue in (FACE_QUEUE, GALLERY_QUEUE):
        model.objects.update_or_create(
            pk=queue, defaults={"pending": 0, "failed": 0, "initialized": True}
        )
    return model.objects


def accepted_original() -> Photo:
    return Photo(
        id="a" * 32,
        gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        original_content_type="image/jpeg",
    )


def test_accepted_original_writes_durable_deltas_only_after_commit(counters):
    product_transaction_active = []

    def record_update(execute, sql, params, many, context):
        if sql.startswith('UPDATE "processing_photoimportcounter"'):
            with psycopg.connect(**connection.get_connection_params()) as reader:
                row = reader.execute("SELECT name FROM picflow_event").fetchone()
            product_transaction_active.append(row is None)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record_update):
        with transaction.atomic():
            Event.objects.create(
                name="Committed original",
                slug="committed-original",
                start_date=date.today(),
                end_date=date.today(),
            )
            schedule_original_accepted(accepted_original())
            assert product_transaction_active == []

    assert product_transaction_active and not any(product_transaction_active)
    assert set(counters.values_list("pk", "pending", "failed")) == {
        (FACE_QUEUE, 1, 0),
        (GALLERY_QUEUE, 1, 0),
    }


def test_rolled_back_original_keeps_durable_counts(counters):
    with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
        schedule_original_accepted(accepted_original())
        raise RuntimeError("rollback")

    assert set(counters.values_list("pk", "pending", "failed")) == {
        (FACE_QUEUE, 0, 0),
        (GALLERY_QUEUE, 0, 0),
    }


def test_concurrent_import_transactions_accumulate_both_deltas(counters):
    scheduled = Barrier(2, timeout=3)

    def accept():
        try:
            with transaction.atomic():
                schedule_original_accepted(accepted_original())
                scheduled.wait()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writers = [pool.submit(accept) for _ in range(2)]
        for writer in writers:
            writer.result(timeout=5)

    assert set(counters.values_list("pk", "pending")) == {(FACE_QUEUE, 2), (GALLERY_QUEUE, 2)}


def test_counter_lock_timeout_cannot_abort_committed_photo(counters):
    actual_sqlstate = []
    uploader = get_user_model().objects.create_user(username="counter-lock-uploader")

    def record_lock_timeout(execute, sql, params, many, context):
        try:
            return execute(sql, params, many, context)
        except Exception as error:
            actual_sqlstate.append(getattr(error.__cause__, "sqlstate", None))
            raise

    with psycopg.connect(**connection.get_connection_params()) as blocker:
        blocker.execute(
            "SELECT 1 FROM processing_photoimportcounter WHERE queue = %s FOR UPDATE",
            [GALLERY_QUEUE],
        )
        with connection.execute_wrapper(record_lock_timeout), transaction.atomic():
            event = Event.objects.create(
                name="Accepted during write contention",
                slug="accepted-during-write-contention",
                start_date=date.today(),
                end_date=date.today(),
            )
            photo = accepted_original()
            photo.event = event
            photo.uploaded_by = uploader
            photo.uploaded_at = timezone.now()
            photo.original_key = "originals/accepted.jpg"
            photo.original_filename = "accepted.jpg"
            photo.original_size = 10
            photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_V1
            photo.save()
            schedule_original_accepted(photo)

    assert actual_sqlstate == [psycopg.errors.LockNotAvailable.sqlstate]
    assert Photo.objects.filter(pk=photo.pk).exists()
    assert set(counters.values_list("pk", "pending")) == {(FACE_QUEUE, 0), (GALLERY_QUEUE, 0)}


def test_failed_manual_rebuild_preserves_previous_rows(counters, monkeypatch):
    from django.core.management.base import CommandError
    from django.db import DatabaseError
    from picflow.management.commands import rebuild_photo_import_counters as command

    counters.filter(pk=FACE_QUEUE).update(pending=9, failed=3)
    monkeypatch.setattr(command, "read_photo_import_backlog_upper_bound", lambda: "z")
    monkeypatch.setattr(command.time, "sleep", lambda _seconds: None)

    def read_page(*, after, upper_bound):
        assert upper_bound == "z"
        if after is None:
            return "a", {FACE_QUEUE: (1, 0), GALLERY_QUEUE: (2, 1)}
        raise DatabaseError("source unavailable")

    monkeypatch.setattr(command, "read_photo_import_backlog_page", read_page)
    with pytest.raises(CommandError, match="Could not rebuild"):
        call_command("rebuild_photo_import_counters")

    assert set(counters.values_list("pk", "pending", "failed", "initialized")) == {
        (FACE_QUEUE, 9, 3, True),
        (GALLERY_QUEUE, 0, 0, True),
    }
