import os
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from urllib.error import URLError
from urllib.request import urlopen

import psycopg
import pytest
from django.db import DatabaseError, connection
from prometheus_client.parser import text_string_to_metric_families


@pytest.mark.django_db(transaction=True)
def test_listener_resubscribes_after_lost_connection_and_keeps_counters():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DB_NAME": str(connection.settings_dict["NAME"])}
    with subprocess.Popen(
        [sys.executable, str(backend / "manage.py"), "run_queue_telemetry", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:

        def wait_for_sample(name, value, *, queue=None):
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    assert process.stderr is not None
                    pytest.fail(process.stderr.read())
                try:
                    with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=1) as response:
                        families = list(text_string_to_metric_families(response.read().decode()))
                except URLError:
                    families = []
                if any(
                    sample.name == name
                    and sample.value == value
                    and (queue is None or sample.labels.get("queue") == queue)
                    for family in families
                    for sample in family.samples
                ):
                    return
                time.sleep(0.05)
            pytest.fail(f"{name} did not reach {value}")

        payload = (
            '{"event":"queue.task.completed","version":1,'
            '"queue":"test_queue","segments_ms":{"execution":1500}}'
        )
        gallery_queue = "photo_import_gallery"
        pending_metric = "findme_photo_import_pending_count"
        delta = (
            '{"event":"queue.backlog.changed","version":2,"photo_id":"delta",'
            '"deltas":{"photo_import_gallery":[1,0]}}'
        )
        try:
            wait_for_sample("findme_queue_telemetry_listening", 1)
            wait_for_sample(pending_metric, 0, queue=gallery_queue)
            with psycopg.connect(**connection.get_connection_params(), autocommit=True) as sender:
                sender.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])
                wait_for_sample("findme_queue_completed_total", 1)
                sender.execute("SELECT pg_notify('queue_telemetry', %s)", [delta])
                wait_for_sample(pending_metric, 1, queue=gallery_queue)
                result = sender.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE application_name = 'findme-queue-telemetry' "
                    "AND datname = current_database()"
                ).fetchall()
                assert result == [(True,)]
                wait_for_sample("findme_queue_telemetry_listening", 0)
                wait_for_sample("findme_queue_telemetry_listening", 1)
                wait_for_sample(pending_metric, 0, queue=gallery_queue)
                sender.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])
                wait_for_sample("findme_queue_completed_total", 2)
                wait_for_sample("findme_queue_segment_duration_seconds_total", 3)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


@pytest.mark.parametrize("interruption", ["database", "stop", "reconnect"])
def test_interrupted_reconciliation_keeps_last_good_snapshot(monkeypatch, interruption):
    from picflow.management.commands import run_queue_telemetry as command

    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    telemetry.set_listening(True)
    stopped = Event()
    monkeypatch.setattr(command.connection, "close", lambda: None)
    monkeypatch.setattr(command, "RECONCILE_PAGE_PAUSE_SECONDS", 0)
    monkeypatch.setattr(command, "read_photo_import_backlog_upper_bound", lambda: None)
    assert command._reconcile_backlogs(telemetry, stopped)
    freshness = _sample(telemetry, "findme_photo_import_last_reconciliation_timestamp_seconds")

    telemetry.record(
        '{"event":"queue.backlog.changed","version":2,"photo_id":"a",'
        '"deltas":{"photo_import_gallery":[1,0]}}'
    )
    monkeypatch.setattr(command, "read_photo_import_backlog_upper_bound", lambda: "c")

    def read_page(*, after, upper_bound):
        assert upper_bound == "c"
        if after == "b":
            if interruption == "database":
                raise DatabaseError("unavailable")
            if interruption == "stop":
                stopped.set()
            if interruption == "reconnect":
                telemetry.set_listening(False)
                telemetry.set_listening(True)
            return "c", {"photo_import_gallery": (7, 0)}
        assert after is None
        return "b", {"photo_import_gallery": (3, 1)}

    monkeypatch.setattr(command, "read_photo_import_backlog_page", read_page)
    assert command._reconcile_backlogs(telemetry, stopped) is (interruption != "database")
    assert (
        _sample(telemetry, "findme_photo_import_last_reconciliation_timestamp_seconds") == freshness
    )
    assert (
        _sample(telemetry, "findme_photo_import_pending_count", queue="photo_import_gallery") == 1
    )


def _sample(telemetry, name, *, queue=None):
    return next(
        sample.value
        for family in telemetry.collect()
        for sample in family.samples
        if sample.name == name and sample.labels.get("queue") == queue
    )


def test_worker_retries_once_with_delay_and_returns_to_regular_interval(monkeypatch):
    from picflow.management.commands import run_queue_telemetry as command

    from config.queue_telemetry import QueueTelemetry

    waits = []
    stopped = Event()

    def wait_for_request(delay):
        waits.append(delay)
        if len(waits) == 4:
            stopped.set()
        return False

    request = Mock(spec=Event)
    request.wait.side_effect = wait_for_request
    reconcile = Mock(side_effect=(False, False, True))
    monkeypatch.setattr(command, "_reconcile_backlogs", reconcile)

    command._run_reconciliations(QueueTelemetry(), request, stopped)
    assert waits == [900, 30, 900, 900]
    assert reconcile.call_count == 3


@pytest.mark.django_db(transaction=True)
def test_postgres_page_wait_does_not_block_notification_or_http():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    from picflow.models import Event as PhotoEvent
    from picflow.models import Photo

    event = PhotoEvent.objects.create(
        name="Reconciliation", slug="reconciliation", start_date=date.today(), end_date=date.today()
    )
    Photo.objects.create(id="a" * 32, event=event, src="legacy.jpg")
    # Give the subprocess a real SQL wait with a lock controlled by this process.
    script = """
import sys
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from picflow.management.commands import run_queue_telemetry

read_page = run_queue_telemetry.read_photo_import_backlog_page

def wait_for_page(*args, **kwargs):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(884271)")
        cursor.execute("SELECT pg_advisory_unlock(884271)")
    return read_page(*args, **kwargs)

run_queue_telemetry.read_photo_import_backlog_page = wait_for_page
call_command("run_queue_telemetry", port=int(sys.argv[1]))
"""
    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DB_NAME": str(connection.settings_dict["NAME"])}
    with psycopg.connect(**connection.get_connection_params(), autocommit=True) as db:
        db.execute("SELECT pg_advisory_lock(884271)")
        lock_held = True
        with subprocess.Popen(
            [sys.executable, "-c", script, str(port)],
            cwd=backend,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        assert process.stderr is not None
                        pytest.fail(process.stderr.read())
                    row = db.execute(
                        "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                        "AND objid = 884271 AND NOT granted"
                    ).fetchone()
                    if row:
                        break
                    time.sleep(0.025)
                else:
                    pytest.fail("reconciliation did not enter the controlled SQL wait")

                for payload in (
                    '{"event":"queue.backlog.changed","version":2,'
                    '"photo_id":"zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",'
                    '"deltas":{"photo_import_gallery":[1,0]}}',
                    '{"event":"queue.task.completed","version":1,"queue":"blocked_page",'
                    '"segments_ms":{"work":1}}',
                ):
                    db.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])

                def wait_for_exposition(sample, *, initial=False):
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=1) as response:
                                metrics = response.read().decode()
                        except URLError:
                            pass
                        else:
                            if initial:
                                assert "findme_photo_import_pending_count" not in metrics
                            if sample in metrics:
                                return
                        time.sleep(0.025)
                    pytest.fail(f"listener failed to expose {sample}")

                wait_for_exposition(
                    'findme_queue_completed_total{queue="blocked_page"} 1.0', initial=True
                )
                db.execute("SELECT pg_advisory_unlock(884271)")
                lock_held = False
                wait_for_exposition(
                    'findme_photo_import_pending_count{queue="photo_import_gallery"} 1.0'
                )
            finally:
                if lock_held:
                    db.execute("SELECT pg_advisory_unlock(884271)")
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
