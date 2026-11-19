import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError
from urllib.request import urlopen

import psycopg
import pytest
from django.db import connection
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
            '{"event":"queue.backlog.changed","version":1,"deltas":{"photo_import_gallery":[1,0]}}'
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


def test_reconciliation_failure_keeps_current_memory_until_next_interval(monkeypatch):
    from picflow.management.commands import run_queue_telemetry as command

    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    first = {"photo_import_gallery": (3, 1), "photo_import_face": (1, 0)}
    second = {"photo_import_gallery": (2, 0), "photo_import_face": (1, 0)}
    snapshots = iter((None, first, None, second))
    observations = []

    class StopListening(Exception):
        pass

    def notifies(*, timeout):
        assert timeout == 900
        observations.append(
            {
                (sample.name, sample.labels.get("queue")): sample.value
                for family in telemetry.collect()
                for sample in family.samples
            }
        )
        if len(observations) == 2:
            yield SimpleNamespace(
                payload=(
                    '{"event":"queue.backlog.changed","version":1,'
                    '"deltas":{"photo_import_gallery":[1,0]}}'
                ),
            )
        if len(observations) == 4:
            raise StopListening

    monkeypatch.setattr(command, "read_photo_import_backlogs", lambda: next(snapshots))
    monkeypatch.setattr(command.connection, "close", lambda: None)
    clock = iter((1000, 2800))
    monkeypatch.setattr(command, "time", SimpleNamespace(time=lambda: next(clock)))

    listener = Mock(spec=psycopg.Connection)
    listener.notifies.side_effect = notifies
    with pytest.raises(StopListening):
        command._observe_notifications(listener, telemetry)
    initial, supplied, stale, reconciled = observations
    freshness = ("findme_photo_import_last_reconciliation_timestamp_seconds", None)
    pending = ("findme_photo_import_pending_count", "photo_import_gallery")
    assert pending not in initial and freshness not in initial
    assert (supplied[pending], supplied[freshness]) == (3, 1000)
    assert (stale[pending], stale[freshness]) == (4, 1000)
    assert (reconciled[pending], reconciled[freshness]) == (2, 2800)
