#!/usr/bin/env python3
"""Verify fresh raw samples for VM, application, or queue telemetry sources."""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("monitoring_control", HERE / "control.py")
assert spec and spec.loader
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
CONFIG = control.load_config()
COUNTERS_READY = "findme_photo_import_counters_ready"
WORK_AVAILABLE = "findme_queue_work_available"
catalog_spec = importlib.util.spec_from_file_location(
    "release_queue_catalog", HERE.parents[2] / "src/backend/config/queue_catalog.py"
)
assert catalog_spec and catalog_spec.loader
catalog = importlib.util.module_from_spec(catalog_spec)
sys.modules[catalog_spec.name] = catalog
catalog_spec.loader.exec_module(catalog)
WORK_QUEUES = tuple(queue.key for queue in catalog.QUEUES)
WORK_STATES = ("waiting", "processing", "retry_wait", "paused")
SOURCES = {
    "vm": (
        {"__name__": "sys_memory_MemAvailable", "instance": "findme-photo-app"},
        {"__name__": "sys_filesystem_FreeB", "instance": "findme-photo-app", "mountpoint": "/"},
        {
            "__name__": "sys_filesystem_FreeB",
            "instance": "findme-photo-app",
            "mountpoint": "/var/lib/findme-photo",
        },
    ),
    "application": (
        {"__name__": "findme_http_requests_total", "job": "findme-http"},
        {"__name__": "pg_up", "job": "findme-postgres"},
    ),
    "queue-telemetry": (
        {"__name__": "findme_queue_results_listener_connected", "job": "findme-queue-telemetry"},
        {"__name__": COUNTERS_READY, "job": "findme-queue-telemetry"},
        *(
            {
                "__name__": f"findme_photo_import_{kind}_count",
                "job": "findme-queue-telemetry",
                "queue": queue,
            }
            for kind in ("pending", "failed")
            for queue in ("photo_import_gallery", "photo_import_face")
        ),
    ),
    "queue-work": (
        *(
            {"__name__": metric, "job": job, "queue": queue}
            for metric, job in (
                ("findme_queue_work_observed_timestamp_seconds", "findme-queue-telemetry"),
                ("findme_queue_ready_observed_timestamp_seconds", "findme-http"),
                ("findme_queue_ready_count", "findme-http"),
                ("findme_queue_results_observed_timestamp_seconds", "findme-queue-telemetry"),
                ("findme_queue_duration_observations_total", "findme-queue-telemetry"),
            )
            for queue in WORK_QUEUES
        ),
        *(
            {
                "__name__": "findme_queue_results_total",
                "job": "findme-queue-telemetry",
                "queue": queue,
                "outcome": outcome,
            }
            for queue in WORK_QUEUES
            for outcome in ("success", "failed", "canceled")
        ),
        *(
            {
                "__name__": "findme_queue_segment_duration_seconds_total",
                "job": "findme-queue-telemetry",
                "queue": queue.key,
                "segment": segment.key,
            }
            for queue in catalog.QUEUES
            for segment in queue.segments
        ),
        *(
            {"__name__": WORK_AVAILABLE, "job": "findme-queue-telemetry", "queue": queue}
            for queue in WORK_QUEUES
        ),
        *(
            {
                "__name__": "findme_queue_work_count",
                "job": "findme-queue-telemetry",
                "queue": queue,
                "state": state,
            }
            for queue in WORK_QUEUES
            for state in WORK_STATES
        ),
    ),
}


def check(transport, scope: str, *, now: float | None = None) -> None:
    selectors = SOURCES[scope]
    results = transport.read_samples(list(selectors), now=now)
    observed_at = time.time() if now is None else now
    if len(results) != len(selectors):
        raise control.ControlError("Monitoring read result count mismatch")
    for selector, series_set in zip(selectors, results, strict=True):
        key = str(selector)
        # Remote Read can return labels for series with no points in the requested window.
        sampled_series = [series for series in series_set if series]
        if not sampled_series:
            raise control.ControlError(f"expected sample missing: {key}")
        for series in sampled_series:
            if any(not math.isfinite(timestamp) for timestamp, _ in series):
                raise control.ControlError(f"sample stale/nonfinite: {key}")
            timestamp, value = max(series, key=lambda point: point[0])
            if not math.isfinite(value) or not 0 <= observed_at - timestamp <= 180:
                raise control.ControlError(f"sample stale/nonfinite: {key}")
            if (
                selector["__name__"].endswith("_observed_timestamp_seconds")
                and not 0 <= observed_at - value <= 150
            ):
                raise control.ControlError(f"sample stale/nonfinite: {key}")
            if selector["__name__"] == "pg_up" and value != 1:
                raise control.ControlError("PostgreSQL exporter cannot connect")
            if selector["__name__"] == "findme_queue_results_listener_connected" and value != 1:
                raise control.ControlError("Queue telemetry listener is disconnected")
            if selector["__name__"] == COUNTERS_READY and value != 1:
                raise control.ControlError("Photo import counters are uninitialized or unavailable")
            if selector["__name__"] == WORK_AVAILABLE and value != 1:
                raise control.ControlError(f"Queue work counter unavailable: {selector['queue']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=tuple(SOURCES))
    parser.add_argument("--oidc-config", type=Path)
    args = parser.parse_args()
    try:
        mode = "github-oidc" if args.oidc_config else "yc"
        transport = control.CloudTransport(CONFIG, control.identity(mode, args.oidc_config))
        deadline = time.monotonic() + 300
        while True:
            try:
                check(transport, args.scope)
                return 0
            except control.ControlError as error:
                if (
                    not str(error).startswith(
                        (
                            "expected sample missing:",
                            "sample stale/nonfinite:",
                            "PostgreSQL exporter",
                            "Queue telemetry listener",
                        )
                    )
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(10)
    except control.ControlError as error:
        print(f"Metric verification failed ({args.scope}): {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Metric verification failed; inspect configuration", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
