import time

import psycopg
from django.conf import settings
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)

_LABEL_NAMES = ("route", "method", "status_class")
_ALLOWED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_DB_ACTIVITY_SQL = """
SELECT
    count(*) FILTER (WHERE state = 'active')::int,
    count(*) FILTER (WHERE state = 'idle')::int,
    count(*) FILTER (WHERE state LIKE 'idle in transaction%')::int,
    count(*) FILTER (WHERE wait_event_type = 'Lock')::int,
    coalesce(max(extract(epoch FROM clock_timestamp() - xact_start))
        FILTER (WHERE state = 'active'), 0)::float,
    coalesce(max(extract(epoch FROM clock_timestamp() - xact_start))
        FILTER (WHERE state LIKE 'idle in transaction%'), 0)::float,
    coalesce(max(extract(epoch FROM clock_timestamp() - query_start))
        FILTER (WHERE wait_event_type = 'Lock'), 0)::float,
    current_setting('max_connections')::int
        - current_setting('superuser_reserved_connections')::int
        - coalesce(current_setting('reserved_connections', true), '0')::int
FROM pg_stat_activity
WHERE datname = current_database()
"""
_DB_VACUUM_SQL = """
SELECT
    coalesce(sum(n_dead_tup), 0)::bigint,
    coalesce(sum(n_live_tup), 0)::bigint,
    max(extract(epoch FROM clock_timestamp() - greatest(last_vacuum, last_autovacuum)))::float,
    (SELECT coalesce(max(age(relfrozenxid)), 0)
     FROM pg_class
     WHERE relkind IN ('r', 'm', 't') AND relfrozenxid <> '0'::xid)::bigint,
    current_setting('autovacuum_freeze_max_age')::bigint
FROM pg_stat_user_tables
"""
_DB_WAL_SQL = "SELECT wal_bytes::bigint FROM pg_stat_wal"

HTTP_REQUESTS = Counter(
    "findme_http_requests",
    "HTTP requests by route, method, and response status class.",
    _LABEL_NAMES,
)
HTTP_REQUEST_DURATION = Histogram(
    "findme_http_request_duration_seconds",
    "HTTP request duration by route, method, and response status class.",
    _LABEL_NAMES,
)
ACCEPTED_PREVIEWS = Counter(
    "findme_accepted_previews",
    "Accepted clean preview publications observed since the current deployment.",
)


def observe_accepted_preview() -> None:
    ACCEPTED_PREVIEWS.inc()


def generate_metrics() -> bytes:
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    database = settings.DATABASES["default"]
    activity = None
    vacuum = None
    wal_bytes = None
    try:
        with psycopg.connect(
            dbname=database["NAME"],
            user=database["USER"],
            password=database["PASSWORD"],
            host=database["HOST"],
            port=database["PORT"],
            connect_timeout=2,
            options="-c statement_timeout=2000",
            autocommit=True,
        ) as connection:
            success = connection.execute("SELECT 1").fetchone() == (1,)
            if success:
                try:
                    activity = connection.execute(_DB_ACTIVITY_SQL).fetchone()
                    vacuum = connection.execute(_DB_VACUUM_SQL).fetchone()
                    wal_row = connection.execute(_DB_WAL_SQL).fetchone()
                    if wal_row is not None:
                        wal_bytes = wal_row[0]
                except psycopg.Error:
                    pass
    except (psycopg.Error, OSError, TimeoutError):
        success = False
    lines = [
        "# HELP findme_db_usable Whether a fresh SQL check can use the application database.",
        "# TYPE findme_db_usable gauge",
        f"findme_db_usable {int(success)}",
        "# HELP findme_db_check_timestamp_seconds Unix time of this SQL check.",
        "# TYPE findme_db_check_timestamp_seconds gauge",
        f"findme_db_check_timestamp_seconds {time.time()}",
    ]
    if activity is not None:
        active, idle, idle_tx, blocked, active_age, idle_tx_age, wait_age, capacity = activity
        lines.extend(
            (
                "# TYPE findme_db_sessions gauge",
                f'findme_db_sessions{{state="active"}} {active}',
                f'findme_db_sessions{{state="idle"}} {idle}',
                f'findme_db_sessions{{state="idle_in_transaction"}} {idle_tx}',
                "# TYPE findme_db_blocked_sessions gauge",
                f"findme_db_blocked_sessions {blocked}",
                "# TYPE findme_db_oldest_transaction_seconds gauge",
                f'findme_db_oldest_transaction_seconds{{state="active"}} {active_age}',
                'findme_db_oldest_transaction_seconds{state="idle_in_transaction"} '
                f"{idle_tx_age}",
                "# TYPE findme_db_lock_wait_oldest_seconds gauge",
                f"findme_db_lock_wait_oldest_seconds {wait_age}",
                "# TYPE findme_db_usable_connections gauge",
                f"findme_db_usable_connections {capacity}",
            )
        )
    if vacuum is not None:
        dead, live, oldest_vacuum, max_xid_age, freeze_max_age = vacuum
        lines.extend(
            (
                "# TYPE findme_db_dead_tuples gauge",
                f"findme_db_dead_tuples {dead}",
                "# TYPE findme_db_live_tuples gauge",
                f"findme_db_live_tuples {live}",
                "# TYPE findme_db_max_relation_xid_age gauge",
                f"findme_db_max_relation_xid_age {max_xid_age}",
                "# TYPE findme_db_autovacuum_freeze_max_age gauge",
                f"findme_db_autovacuum_freeze_max_age {freeze_max_age}",
            )
        )
        if oldest_vacuum is not None:
            lines.extend(
                (
                    "# TYPE findme_db_oldest_vacuum_seconds gauge",
                    f"findme_db_oldest_vacuum_seconds {oldest_vacuum}",
                )
            )
    if wal_bytes is not None:
        lines.extend(
            (
                "# TYPE findme_db_wal_bytes_total counter",
                f"findme_db_wal_bytes_total {wal_bytes}",
            )
        )
    return generate_latest(registry) + ("\n".join(lines) + "\n").encode()


class HttpMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/metrics/":
            return self.get_response(request)

        started_at = time.perf_counter()
        response = self.get_response(request)
        labels = {
            "route": self._route_name(request),
            "method": self._method_name(request),
            "status_class": f"{response.status_code // 100}xx",
        }
        HTTP_REQUESTS.labels(**labels).inc()
        HTTP_REQUEST_DURATION.labels(**labels).observe(time.perf_counter() - started_at)
        return response

    @staticmethod
    def _route_name(request) -> str:
        if request.resolver_match is None:
            return "unmatched"
        return request.resolver_match.view_name or "unmatched"

    @staticmethod
    def _method_name(request) -> str:
        if request.method in _ALLOWED_HTTP_METHODS:
            return request.method
        return "other"
