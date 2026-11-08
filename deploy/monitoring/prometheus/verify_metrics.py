#!/usr/bin/env python3
"""Verify fresh raw samples for the released VM or application sources."""

from __future__ import annotations

import argparse
import importlib.util
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
        'sys_memory_MemAvailable{instance="findme-photo-app"}',
        'sys_filesystem_FreeB{instance="findme-photo-app",mountpoint="/"}',
        'sys_filesystem_FreeB{instance="findme-photo-app",mountpoint="/var/lib/findme-photo"}',
    ),
    "application": (
        'findme_http_requests_total{job="findme-http"}',
        'pg_up{job="findme-postgres"}',
    ),
}


def check(transport, scope: str, *, now: float | None = None) -> None:
    for source in SOURCES[scope]:
        samples = control._fresh_matrix(
            transport, source + "[180s]", key=source, max_age=180, now=now
        )
        if source.startswith("pg_up") and any(value != 1 for _, value in samples):
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
        print(f"Metric verification failed: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Metric verification failed; inspect configuration", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
