#!/usr/bin/env python3
"""Verify fresh raw samples for the released VM or application sources."""

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
        {"__name__": "findme_gallery_search_queue_depth", "job": "findme-http"},
        {"__name__": "findme_gallery_search_completed_total", "job": "findme-http"},
        {
            "__name__": "findme_gallery_search_phase_duration_seconds_total",
            "job": "findme-http",
            "phase": "queue",
        },
        {
            "__name__": "findme_gallery_search_phase_duration_seconds_total",
            "job": "findme-http",
            "phase": "processing",
        },
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
        if not series_set:
            raise control.ControlError(f"expected sample missing: {key}")
        for series in series_set:
            if not series:
                raise control.ControlError(f"expected sample missing: {key}")
            if any(not math.isfinite(timestamp) for timestamp, _ in series):
                raise control.ControlError(f"sample stale/nonfinite: {key}")
            timestamp, value = max(series, key=lambda point: point[0])
            if not math.isfinite(value) or not 0 <= observed_at - timestamp <= 180:
                raise control.ControlError(f"sample stale/nonfinite: {key}")
            if selector["__name__"] == "pg_up" and value != 1:
                raise control.ControlError("PostgreSQL exporter cannot connect")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=tuple(SOURCES))
    parser.add_argument("--oidc-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        transport = control.CloudTransport(
            CONFIG, control.identity("github-oidc", args.oidc_config)
        )
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
