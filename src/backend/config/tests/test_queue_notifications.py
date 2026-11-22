from threading import Event

import psycopg
import pytest
from django.db import connection

from config.queue_notifications import listen, wait_for_notifications


@pytest.mark.django_db(transaction=True)
def test_notifications_between_scan_and_wait_and_full_packet_are_retained():
    with listen("notification_test", application_name="findme-notification-test") as listener:
        # The producer commits after the initial empty queue scan, before wait starts.
        with psycopg.connect(**connection.get_connection_params(), autocommit=True) as sender:
            sender.execute("SELECT pg_notify('notification_test', 'first')")
            sender.execute("SELECT pg_notify('notification_test', 'second')")
        received = wait_for_notifications(listener, timeout=1)
        if len(received) == 1:
            received += wait_for_notifications(listener, timeout=1)
        assert [notification.payload for notification in received] == ["first", "second"]
        assert wait_for_notifications(listener, timeout=0.01) == []


@pytest.mark.django_db(transaction=True)
def test_archive_slot_reconnects_and_rescans_after_listener_disconnect():
    from unittest.mock import patch

    from picflow.management.commands import run_archive_worker as worker

    stopping = Event()
    scans = []
    disconnected = False

    def process():
        scans.append(True)
        if len(scans) == 2:
            stopping.set()
        return False

    def disconnect(listener, *, timeout):
        nonlocal disconnected
        assert not disconnected
        disconnected = True
        with psycopg.connect(**connection.get_connection_params(), autocommit=True) as sender:
            sender.execute("SELECT pg_terminate_backend(%s)", [listener.info.backend_pid])
        return wait_for_notifications(listener, timeout=timeout)

    with (
        patch.object(worker, "process_next_archive", side_effect=process),
        patch.object(worker, "wait_for_notifications", side_effect=disconnect),
        patch.object(worker, "_RECONNECT_SECONDS", 0),
        patch.object(worker.connections, "close_all"),
    ):
        worker._run_slot(stopping)
    assert scans == [True, True]


@pytest.mark.django_db(transaction=True)
def test_archive_subscribes_before_scan_and_retains_signal_until_wait():
    from unittest.mock import patch

    from picflow.management.commands import run_archive_worker as worker

    stopping = Event()
    scans = []

    def process():
        scans.append(True)
        if len(scans) == 1:
            # The queue changed after the scan's snapshot, before waiting began.
            with psycopg.connect(**connection.get_connection_params(), autocommit=True) as sender:
                sender.execute("SELECT pg_notify('archive_jobs', '')")
        else:
            stopping.set()
        return False

    def wait(listener, *, timeout):
        notifications = wait_for_notifications(listener, timeout=timeout)
        assert notifications, "subscription must precede the first scan"
        return notifications

    with (
        patch.object(worker, "process_next_archive", side_effect=process),
        patch.object(worker, "wait_for_notifications", side_effect=wait),
        patch.object(worker.connections, "close_all"),
    ):
        worker._run_slot(stopping)
    assert scans == [True, True]
