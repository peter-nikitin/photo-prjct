"""PostgreSQL notification transport shared by queue owners and telemetry."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from django.db import connection
from psycopg import sql

ARCHIVE_CHANNEL = "archive_jobs"
MATCHING_CHANNEL = "face_search_jobs"


def notify_work(channel: str) -> None:
    """Signal inside the work transaction: PostgreSQL delivers only at commit."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_notify(%s, '')", [channel])


@contextmanager
def listen(channel: str, *, application_name: str) -> Iterator[psycopg.Connection]:
    """Subscribe before scanning work, on a dedicated autocommit connection."""
    params = connection.get_connection_params() | {
        "autocommit": True,
        "application_name": application_name,
        "connect_timeout": 2,
        "options": "-c statement_timeout=2000",
    }
    with psycopg.connect(**params) as listener:
        listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
        yield listener


def wait_for_notifications(
    listener: psycopg.Connection, *, timeout: float | None = None
) -> list[psycopg.Notify]:
    # Consume the full received packet. Breaking after its first notification would
    # discard the remaining notifications in psycopg's generator.
    return list(listener.notifies(timeout=timeout, stop_after=1))
