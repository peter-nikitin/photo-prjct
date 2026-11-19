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
WEB_VITALS_PAGE_BY_ROUTE = {
    "event_catalog": "catalog",
    "event_detail": "gallery",
    "legal": "legal",
    "photographer_login": "photographer_login",
    "commerce:detail": "cart",
    "commerce:order_list": "orders",
}
WEB_VITALS_VIEWPORTS = ("narrow", "wide")
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

_FACE_SEARCH_P75_SQL = """
SELECT
    CASE configuration->>'processor' WHEN 'selfie_query' THEN 'selfie' ELSE 'gallery' END,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY extract(epoch FROM terminal_at - created_at))
FROM selfie_search_selfiesearch
WHERE status = 'ready'
    AND terminal_at > statement_timestamp() - interval '24 hours'
    AND terminal_at <= statement_timestamp()
    AND terminal_at >= created_at
    AND configuration->>'processor' IN ('selfie_query', 'gallery_photo_query')
GROUP BY 1
"""

_GALLERY_SEARCH_SQL = """
SELECT count(*) FROM selfie_search_facesearchtask
WHERE source = 'gallery' AND status = 'queued'
"""

_SELFIE_SEARCH_SQL = """
SELECT
    (SELECT count(*) FROM selfie_search_selfiesearchjob
        WHERE status IN ('queued', 'retry_wait')),
    (SELECT count(*) FROM selfie_search_facesearchtask
        WHERE source = 'selfie' AND status = 'queued')
"""


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
IMPORTED_PHOTOS = Counter(
    "findme_imported_photos",
    "New photos successfully imported from Yandex Disk.",
)
WEB_VITALS_LCP = Histogram(
    "findme_web_vitals_lcp_seconds",
    "Largest Contentful Paint in seconds.",
    ("page", "viewport"),
    buckets=(0.5, 1, 1.5, 2.5, 4, 8, 16, 32, float("inf")),
)
WEB_VITALS_INP = Histogram(
    "findme_web_vitals_inp_seconds",
    "Interaction to Next Paint in seconds.",
    ("page", "viewport"),
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 4, float("inf")),
)
WEB_VITALS_CLS = Histogram(
    "findme_web_vitals_cls",
    "Cumulative Layout Shift score.",
    ("page", "viewport"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, float("inf")),
)

WEB_VITALS_TTFB = Histogram(
    "findme_web_vitals_ttfb_seconds",
    "Navigation time to first byte in seconds.",
    ("page", "viewport"),
    buckets=(0.05, 0.1, 0.25, 0.5, 0.8, 1.2, 1.8, 2.5, 4, 8, 16, 32, float("inf")),
)


def collect_queue_samples() -> dict[str, tuple[int, float, float, float]]:
    # Metrics are imported by Gunicorn before Django has populated its app registry.
    from config.queue_metrics import collect_queue_samples as collect

    return collect()


def observe_accepted_preview() -> None:
    ACCEPTED_PREVIEWS.inc()


def observe_imported_photo() -> None:
    IMPORTED_PHOTOS.inc()


def generate_metrics() -> bytes:
    for histogram in (WEB_VITALS_LCP, WEB_VITALS_INP, WEB_VITALS_CLS, WEB_VITALS_TTFB):
        for page in WEB_VITALS_PAGE_BY_ROUTE.values():
            for viewport in WEB_VITALS_VIEWPORTS:
                histogram.labels(page=page, viewport=viewport)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    database = settings.DATABASES["default"]
    activity = None
    vacuum = None
    wal_bytes = None
    gallery = None
    selfie = None
    face_search_p75 = []
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
                try:
                    gallery = connection.execute(_GALLERY_SEARCH_SQL).fetchone()
                except psycopg.Error:
                    pass
                try:
                    selfie = connection.execute(_SELFIE_SEARCH_SQL).fetchone()
                except psycopg.Error:
                    pass
                try:
                    face_search_p75 = connection.execute(_FACE_SEARCH_P75_SQL).fetchall()
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
    if gallery is not None:
        lines.extend(
            (
                "# HELP findme_gallery_search_queue_depth "
                "Gallery searches waiting, including retries.",
                "# TYPE findme_gallery_search_queue_depth gauge",
                f"findme_gallery_search_queue_depth {gallery[0]}",
            )
        )
    if selfie is not None:
        recognition, matching = selfie
        lines.extend(
            (
                "# TYPE findme_selfie_search_queue_depth gauge",
                f'findme_selfie_search_queue_depth{{stage="recognition"}} {recognition}',
                f'findme_selfie_search_queue_depth{{stage="matching"}} {matching}',
            )
        )
    if face_search_p75:
        lines.append("# TYPE findme_face_search_total_duration_p75_seconds gauge")
        lines.extend(
            f'findme_face_search_total_duration_p75_seconds{{source="{source}"}} {seconds}'
            for source, seconds in face_search_p75
        )
    lines.extend(
        (
            "# TYPE findme_queue_ready_count gauge",
            "# TYPE findme_queue_oldest_ready_age_seconds gauge",
            "# TYPE findme_queue_last_success_timestamp_seconds gauge",
            "# TYPE findme_queue_observed_timestamp_seconds gauge",
        )
    )
    for queue, (count, age, success_at, observed_at) in (
        collect_queue_samples().items() if success else ()
    ):
        lines.extend(
            (
                f'findme_queue_ready_count{{queue="{queue}"}} {count}',
                f'findme_queue_oldest_ready_age_seconds{{queue="{queue}"}} {age}',
                f'findme_queue_last_success_timestamp_seconds{{queue="{queue}"}} {success_at}',
                f'findme_queue_observed_timestamp_seconds{{queue="{queue}"}} {observed_at}',
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
        if request.resolver_match is not None and request.resolver_match.view_name == "web_vitals":
            return response
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
