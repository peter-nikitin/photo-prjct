import os
import socket
import subprocess
import sys
import time
from pathlib import Path
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

        def wait_for_sample(name, value):
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
                    sample.name == name and sample.value == value
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
        try:
            wait_for_sample("findme_queue_telemetry_listening", 1)
            with psycopg.connect(**connection.get_connection_params(), autocommit=True) as sender:
                sender.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])
                wait_for_sample("findme_queue_completed_total", 1)
                result = sender.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE application_name = 'findme-queue-telemetry' "
                    "AND datname = current_database()"
                ).fetchall()
                assert result == [(True,)]
                wait_for_sample("findme_queue_telemetry_listening", 0)
                wait_for_sample("findme_queue_telemetry_listening", 1)
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
