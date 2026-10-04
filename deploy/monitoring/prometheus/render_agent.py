#!/usr/bin/env python3
"""Render additive routes for an existing Unified Agent; no host changes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

STORAGE = "findme_prometheus_buffer"
CHANNEL = "findme_prometheus_remote_write"


def merge_agent(
    native: dict[str, Any], *, role: str, workspace_id: str, worker_telemetry: bool = False
) -> dict[str, Any]:
    if role not in ("canonical", "public") or not re.fullmatch(r"[A-Za-z0-9_-]+", workspace_id):
        raise ValueError("valid role and workspace ID required")
    if type(worker_telemetry) is not bool or (worker_telemetry and role != "canonical"):
        raise ValueError("worker telemetry requires the canonical role")
    result = copy.deepcopy(native)
    for section, name in [("storages", STORAGE), ("channels", CHANNEL)]:
        result[section] = [item for item in result.get(section, []) if item.get("name") != name]
    result["routes"] = [
        route
        for route in result.get("routes", [])
        if route.get("channel", {}).get("channel_ref", {}).get("name") != CHANNEL
    ]
    result.setdefault("storages", []).append(
        {
            "name": STORAGE,
            "plugin": "fs",
            "config": {
                "directory": "/var/lib/yandex/unified_agent/findme_prometheus_buffer",
                "max_partition_size": "100mb",
                "max_segment_size": "10mb",
            },
        }
    )
    result.setdefault("channels", []).append(
        {
            "name": CHANNEL,
            "channel": {
                "pipe": [{"storage_ref": {"name": STORAGE}}],
                "output": {
                    "plugin": "metrics",
                    "config": {
                        "url": f"https://monitoring.api.cloud.yandex.net/prometheus/workspaces/{workspace_id}/api/v1/write",
                        "set_host_label": None,
                        "iam": {"cloud_meta": {}},
                    },
                },
            },
        }
    )
    inputs = [
        {
            "plugin": "metrics_pull",
            "config": {
                "url": "http://127.0.0.1:19091/metrics",
                "format": {"prometheus": {}},
                "poll_period": "300s" if role == "public" else "60s",
                "timeout": "30s" if role == "public" else "40s",
                "prometheus_config": {
                    "job_name": "findme-public" if role == "public" else "findme-commerce"
                },
            },
        }
    ]
    if role == "canonical":
        inputs += [
            {
                "plugin": "linux_metrics",
                "config": {
                    "poll_period": "60s",
                    "namespace": "sys",
                    "prometheus_config": {"job_name": "findme-linux"},
                },
            },
            {
                "plugin": "metrics_pull",
                "config": {
                    "url": "http://127.0.0.1:8080/metrics/",
                    "format": {"prometheus": {}},
                    "poll_period": "60s",
                    "timeout": "10s",
                    "prometheus_config": {"job_name": "findme-http"},
                },
            },
            {
                "plugin": "metrics_pull",
                "config": {
                    "url": "http://127.0.0.1:9187/metrics",
                    "format": {"prometheus": {}},
                    "poll_period": "60s",
                    "timeout": "10s",
                    "prometheus_config": {"job_name": "findme-postgres"},
                },
            },
        ]
    else:
        inputs += [
            {
                "plugin": "linux_metrics",
                "config": {
                    "poll_period": "60s",
                    "namespace": "sys",
                    "prometheus_config": {"job_name": "findme-image-linux"},
                },
            },
            {
                "plugin": "metrics_pull",
                "config": {
                    "url": "http://127.0.0.1:18081/metrics",
                    "format": {"prometheus": {}},
                    "poll_period": "60s",
                    "timeout": "10s",
                    "namespace": "origin",
                    "prometheus_config": {"job_name": "findme-image-origin"},
                },
            },
            {
                "plugin": "metrics_pull",
                "config": {
                    "url": "http://127.0.0.1:18081/imgproxy-metrics",
                    "format": {"prometheus": {}},
                    "poll_period": "60s",
                    "timeout": "10s",
                    "namespace": "imgproxy",
                    "prometheus_config": {"job_name": "findme-imgproxy"},
                },
            },
        ]
    for item in inputs:
        result.setdefault("routes", []).append(
            {"input": item, "channel": {"channel_ref": {"name": CHANNEL}}}
        )
    if worker_telemetry:
        result["routes"].append(
            {
                "input": {
                    "plugin": "metrics_pull",
                    "config": {
                        "url": "http://127.0.0.1:8080/worker-diagnostics/metrics/",
                        "format": {"prometheus": {}},
                        "poll_period": "30s",
                        "timeout": "10s",
                        "prometheus_config": {"job_name": "findme-worker-diagnostics"},
                    },
                },
                "channel": {
                    "pipe": [
                        {
                            "filter": {
                                "plugin": "transform_metric_labels",
                                "config": {
                                    "labels": [{"job": "-"}, {"instance": "-"}, {"host": "-"}]
                                },
                            }
                        }
                    ],
                    "channel_ref": {"name": CHANNEL},
                },
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--role", choices=("canonical", "public"), required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--worker-telemetry", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    content = args.current.read_bytes()
    config = merge_agent(
        yaml.safe_load(content),
        role=args.role,
        workspace_id=args.workspace_id,
        worker_telemetry=args.worker_telemetry,
    )
    args.output.write_text(yaml.safe_dump(config, sort_keys=False))
    print("Current configuration sha256: " + hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    main()
