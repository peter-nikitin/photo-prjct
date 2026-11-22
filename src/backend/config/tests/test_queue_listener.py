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
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from prometheus_client.parser import text_string_to_metric_families


@pytest.mark.django_db(transaction=True)
def test_counter_scrapes_survive_listener_disconnect_and_restart():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DB_NAME": str(connection.settings_dict["NAME"])}
    command = [
        sys.executable,
        str(backend / "manage.py"),
        "run_queue_telemetry",
        "--port",
        str(port),
    ]

    with subprocess.Popen(
        command,
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
            '"queue":"gallery_search","segments_ms":{"execution":1500}}'
        )
        gallery_queue = "photo_import_gallery"
        pending_metric = "findme_photo_import_pending_count"
        readiness = "findme_photo_import_counters_ready"
        try:
            wait_for_sample("findme_queue_results_listener_connected", 1)
            wait_for_sample(readiness, 0)
            model = apps.get_model("processing", "PhotoImportCounter")
            for queue in (gallery_queue, "photo_import_face"):
                model.objects.get_or_create(pk=queue)
            call_command("rebuild_photo_import_counters")
            model.objects.filter(pk=gallery_queue).update(pending=7)
            wait_for_sample(pending_metric, 7, queue=gallery_queue)
            with psycopg.connect(**connection.get_connection_params(), autocommit=True) as sender:
                sender.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])
                wait_for_sample("findme_journey_completed_total", 1)
                with psycopg.connect(**connection.get_connection_params()) as blocker:
                    blocker.execute(
                        "LOCK TABLE processing_photoimportcounter IN ACCESS EXCLUSIVE MODE"
                    )
                    wait_for_sample(readiness, 0)
                    sender.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])
                    wait_for_sample("findme_journey_completed_total", 2)
                wait_for_sample(pending_metric, 7, queue=gallery_queue)
                result = sender.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE application_name = 'findme-queue-telemetry' "
                    "AND datname = current_database()"
                ).fetchall()
                assert result == [(True,)]
                wait_for_sample("findme_queue_results_listener_connected", 0)
                wait_for_sample(pending_metric, 7, queue=gallery_queue)
                wait_for_sample("findme_queue_results_listener_connected", 1)
                wait_for_sample(pending_metric, 7, queue=gallery_queue)
                sender.execute("SELECT pg_notify('queue_telemetry', %s)", [payload])
                wait_for_sample("findme_journey_completed_total", 3)
                wait_for_sample("findme_journey_segment_duration_seconds_total", 4.5)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    with subprocess.Popen(command, env=env, stderr=subprocess.PIPE, text=True) as process:
        try:
            wait_for_sample("findme_queue_results_listener_connected", 1)
            wait_for_sample(pending_metric, 7, queue=gallery_queue)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
