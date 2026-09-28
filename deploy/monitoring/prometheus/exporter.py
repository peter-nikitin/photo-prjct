#!/usr/bin/env python3
"""Fresh bounded observations on one loopback-only Prometheus endpoint."""

from __future__ import annotations

import argparse
import json
import math
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import monitor_commerce as commerce  # noqa: E402
import monitor_public_health as probe  # noqa: E402

PORT = 19091
PUBLIC_TARGET = "https://findme-photo.ru/health/"
METRICS = {
    "public": {"findme_probe_success", "findme_probe_duration_seconds"},
    "canonical": {"commerce_worker_alive", "commerce_oldest_ready_age_seconds"},
}


def collect(
    role: str, *, public_runner: Any = None, commerce_runner: Any = None
) -> tuple[int, str]:
    samples: list[dict[str, Any]] = []

    def capture(config: Any, metrics: Any) -> None:
        samples.extend(metrics)

    try:
        if role == "public":
            (public_runner or probe.run_probe)(
                probe.ProbeConfig(PUBLIC_TARGET, "", "canonical-health", timeout_seconds=10),
                metric_writer=capture,
                emit=lambda message: None,
            )
        elif role == "canonical":
            status = (commerce_runner or commerce.run)(
                commerce.Config("", "/opt/photo-prjct"),
                metric_writer=capture,
                emit=lambda message: None,
            )
            if status:
                raise ValueError("observation failed")
        else:
            raise ValueError("invalid host role")
        names = {metric["name"] for metric in samples}
        if not METRICS[role] <= names:
            raise ValueError("incomplete observation")
        allowed = METRICS[role] | (
            {"findme_probe_tls_days_remaining"} if role == "public" else set()
        )
        lines = []
        for metric in samples:
            name, value = metric["name"], float(metric["value"])
            labels = metric["labels"]
            if name not in allowed or not math.isfinite(value) or set(labels) != {"check"}:
                raise ValueError("invalid bounded metric")
            lines.extend(
                [f"# TYPE {name} gauge", f"{name}{{check={json.dumps(labels['check'])}}} {value}"]
            )
        return 200, "\n".join(lines) + "\n"
    except Exception:
        return 503, "observation unavailable\n"


def create_server(role: str, *, port: int = PORT) -> HTTPServer:
    if role not in METRICS:
        raise ValueError("invalid host role")

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(45)

        def do_GET(self) -> None:
            if self.path != "/metrics":
                self.send_error(404)
                return
            status, body = collect(role)
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return HTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=tuple(METRICS), required=True)
    args = parser.parse_args()
    with create_server(args.role) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
