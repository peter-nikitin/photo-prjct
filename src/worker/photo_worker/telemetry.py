"""Bounded in-memory runtime diagnostics, separate from processing authority."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from typing import Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)

disable_created_metrics()

KINDS = frozenset(
    {
        "capture_metadata",
        "generate_preview",
        "generate_watermarked_preview",
        "face_embedding",
        "selfie_query",
        "bib_recognition",
    }
)
Outcome = Literal["callback_delivered", "execution_failed", "transport_failed", "lease_lost"]
OUTCOMES = frozenset({"callback_delivered", "execution_failed", "transport_failed", "lease_lost"})
DURATION_BUCKETS = (1, 5, 15, 60, 300, 900, 1800)
RUNTIME_PORT = 9101


class RuntimeTelemetry:
    """One process with bounded in-flight execution counts and no arbitrary labels."""

    def __init__(self, generation: Callable[[], str | None]) -> None:
        self._generation = generation
        self._current_generation: str | None = None
        self._lock = threading.Lock()
        self._executing: dict[int, tuple[float, str | None]] = {}
        self._reset()

    def _reset(self) -> None:
        self._registry = CollectorRegistry()
        self._busy = Gauge(
            "worker_runtime_busy",
            "Number of admitted executions being handled.",
            registry=self._registry,
        )
        self._executions = Counter(
            "worker_runtime_executions",
            "Terminal runtime observations, not accepted product results.",
            ["kind", "outcome"],
            registry=self._registry,
        )
        self._duration = Histogram(
            "worker_runtime_execution_duration_seconds",
            "Execution elapsed time including result delivery.",
            ["kind", "outcome"],
            buckets=DURATION_BUCKETS,
            registry=self._registry,
        )
        self._busy.set(len(self._executing))

    def _sync_generation(self) -> str | None:
        generation = self._generation()
        if generation != self._current_generation:
            self._current_generation = generation
            self._reset()
        return generation

    def started(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError("unsupported runtime kind")
        with self._lock:
            generation = self._sync_generation()
            self._executing[threading.get_ident()] = (monotonic(), generation)
            self._busy.set(len(self._executing))

    def finished(self, kind: str, outcome: str, duration_seconds: float | None = None) -> None:
        if kind not in KINDS or outcome not in OUTCOMES:
            raise ValueError("unsupported runtime kind or outcome")
        with self._lock:
            generation = self._sync_generation()
            execution = self._executing.pop(threading.get_ident(), None)
            try:
                if execution is None or generation != execution[1]:
                    # An old in-flight attempt cannot contribute to a new registration.
                    return
                if duration_seconds is None:
                    duration_seconds = max(0.0, monotonic() - execution[0])
                self._executions.labels(kind, outcome).inc()
                self._duration.labels(kind, outcome).observe(duration_seconds)
            finally:
                self._busy.set(len(self._executing))

    def scrape(self) -> tuple[str | None, bytes]:
        with self._lock:
            # Generation and samples are one snapshot. A concurrent re-registration makes
            # this snapshot old (and rejectable), never labels old counters as new work.
            generation = self._sync_generation()
            return generation, generate_latest(self._registry)


def start_runtime_server(
    telemetry: RuntimeTelemetry, *, port: int = RUNTIME_PORT, host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    """The container entry point explicitly binds its namespace; host mapping is loopback."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/metrics", "/status"}:
                self.send_error(404)
                return
            try:
                generation, metrics = telemetry.scrape()
            except Exception:
                self.send_error(503)
                return
            if generation is None:
                self.send_error(503)
                return
            body = (
                metrics
                if self.path == "/metrics"
                else json.dumps({"registration_generation": generation}).encode()
            )
            self.send_response(200)
            self.send_header(
                "Content-Type",
                CONTENT_TYPE_LATEST if self.path == "/metrics" else "application/json",
            )
            self.send_header("X-Worker-Registration-Generation", generation)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Never log request paths or client-controlled values.
            return

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
