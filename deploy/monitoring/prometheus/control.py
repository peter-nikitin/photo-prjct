#!/usr/bin/env python3
"""Offline rendering and explicit reconciliation of FindMe's monitoring objects."""

from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OWNED_RULES = "findme-photo.yml"
API = "https://monitoring.api.cloud.yandex.net"
PROMETHEUS_VERSION = "3.5.0"
WORKER_POOLS = ("bulk", "selfie")
WORKER_SOURCE_MAX_AGE = 90
WORKER_POOL_METRICS = {
    "queue_available": "worker_pool_queue_observation_available",
    "queue_timestamp": "worker_pool_queue_observation_timestamp_seconds",
    "cloud_timestamp": "worker_pool_cloud_observation_timestamp_seconds",
    "publisher_timestamp": "worker_pool_native_publisher_success_timestamp_seconds",
    "claimable": "worker_pool_claimable",
    "oldest_age": "worker_pool_oldest_claimable_age_seconds",
    "workload": "worker_pool_workload",
    "running": "worker_pool_running_instances",
    "expected": "worker_pool_expected_instances",
}
WORKER_NODE_METRICS = (
    "worker_node_cloud_observation_timestamp_seconds",
    "worker_host_observation_missing",
    "worker_host_observation_fresh",
    "worker_host_collection_available",
    "worker_runtime_scrape_available",
    "worker_runtime_observation_fresh",
)


class ControlError(Exception):
    """Safe operator error: never includes transport response bodies or credentials."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or HERE / "environment.json").read_text())


def validate_config(config: dict[str, Any], *, live: bool = False) -> None:
    if type(config.get("worker_alerts_enabled")) is not bool:
        raise ControlError("worker_alerts_enabled must be boolean")
    if type(config.get("image_origin_alerts_enabled")) is not bool:
        raise ControlError("image_origin_alerts_enabled must be boolean")
    for field in (
        "folder_id",
        "dashboard_id",
        "channel_id",
        "image_origin_host",
        "image_cdn_resource",
    ) + (("workspace_id", "channel_name", "telegram_channel_name") if live else ()):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ControlError(f"activation requires {field}")
    for field in ("folder_id", "dashboard_id", "workspace_id"):
        if config.get(field) and not re.fullmatch(r"[a-zA-Z0-9_-]+", config[field]):
            raise ControlError(f"invalid {field}")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", config["image_origin_host"]):
        raise ControlError("invalid image_origin_host")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", config["image_cdn_resource"]):
        raise ControlError("invalid image_cdn_resource")
    for metric in config["metrics"].values():
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", metric["name"]):
            raise ControlError("invalid metric name")
        if metric["type"] not in ("gauge", "counter", "histogram"):
            raise ControlError("invalid metric type")
        if not 1 <= metric["max_age"] <= 600:
            raise ControlError("invalid freshness bound")
        for label, value in metric["labels"].items():
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", label) or not isinstance(value, str):
                raise ControlError("invalid metric label")
    if live and (
        config["cpu_semantics"] != "cumulative_counter"
        or any(config["metrics"][key]["type"] != "counter" for key in ("cpu_useful", "cpu_idle"))
    ):
        raise ControlError("CPU cumulative-counter semantics require verified live evidence")
    if config["metrics"]["http_requests"]["type"] != "counter":
        raise ControlError("HTTP cumulative counter contract required")


def selectors(config: dict[str, Any]) -> dict[str, str]:
    def selector(name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        return (
            name
            + "{"
            + ",".join(f"{key}={json.dumps(value)}" for key, value in sorted(labels.items()))
            + "}"
        )

    result = {
        key: selector(value["name"], value["labels"]) for key, value in config["metrics"].items()
    }
    requests = config["metrics"]["http_requests"]
    result["http_5xx"] = selector(requests["name"], {**requests["labels"], "status_class": "5xx"})
    return result


def expressions(config: dict[str, Any]) -> dict[str, str]:
    s = selectors(config)
    return {
        "public": f"max_over_time({s['public_success']}[10m])",
        "tls": f"min_over_time({s['tls_days']}[10m])",
        "disk_percent": f"100 * {s['disk_free']} / {s['disk_size']}",
        "disk_bytes": s["disk_free"],
        "swap_free_gib": f"{s['swap_free']} / 1073741824",
        "swap_total_gib": f"{s['swap_total']} / 1073741824",
        "inode_percent": f"100 * {s['inode_free']} / {s['inode_total']}",
        "disk_read_rate": f"rate({s['disk_read']}[5m])",
        "disk_write_rate": f"rate({s['disk_write']}[5m])",
        "network_rx_rate": f"rate({s['network_rx']}[5m])",
        "network_tx_rate": f"rate({s['network_tx']}[5m])",
        "memory": f"100 * {s['memory_available']} / {s['memory_total']}",
        "cpu": (
            f"100 * rate({s['cpu_useful']}[5m]) / "
            f"(rate({s['cpu_useful']}[5m]) + rate({s['cpu_idle']}[5m]))"
        ),
        "http_total": f"sum(increase({s['http_requests']}[5m]))",
        "http_errors": (
            f"(sum(increase({s['http_5xx']}[5m])) or (0 * sum(increase({s['http_requests']}[5m]))))"
        ),
        "commerce": f"max_over_time({s['commerce_alive']}[5m])",
        "commerce_window_age": f"max_over_time({s['commerce_age']}[5m])",
    }


def image_selectors() -> dict[str, str]:
    linux = '{job="findme-image-linux"}'
    origin = '{job="findme-image-origin"}'
    proxy = '{job="findme-imgproxy"}'
    return {
        "image_cpu_useful": "sys_system_UsefulTime" + linux,
        "image_cpu_idle": "sys_system_IdleTime" + linux,
        "image_mem_available": "sys_memory_MemAvailable" + linux,
        "image_mem_total": "sys_memory_MemTotal" + linux,
        "image_disk_free": 'sys_filesystem_FreeB{job="findme-image-linux",mountpoint="/"}',
        "image_disk_size": 'sys_filesystem_SizeB{job="findme-image-linux",mountpoint="/"}',
        "image_responses": "origin_image_origin_responses_total" + origin,
        "image_5xx": (
            'origin_image_origin_responses_total{job="findme-image-origin",status_class="5xx"}'
        ),
        "image_2xx": (
            'origin_image_origin_responses_total{job="findme-image-origin",status_class="2xx"}'
        ),
        "image_limited": "origin_image_origin_limited_total" + origin,
        "image_auth_rejected": "origin_image_origin_auth_rejected_total" + origin,
        "image_proxy_requests": "imgproxy_requests_total" + proxy,
        "image_proxy_errors": "imgproxy_errors_total" + proxy,
        "image_proxy_duration_count": "imgproxy_request_duration_seconds_count" + proxy,
        "image_proxy_duration_bucket": (
            'imgproxy_request_duration_seconds_bucket{job="findme-imgproxy"}'
        ),
    }


def image_expressions() -> dict[str, str]:
    s = image_selectors()
    total = f"sum(increase({s['image_responses']}[5m]))"
    errors = f"sum(increase({s['image_5xx']}[5m]))"
    limited = f"sum(increase({s['image_limited']}[5m]))"
    cpu_useful = f"rate({s['image_cpu_useful']}[5m])"
    cpu_idle = f"rate({s['image_cpu_idle']}[5m])"
    duration = s["image_proxy_duration_bucket"]
    return {
        "image_requests_5m": total,
        "image_5xx_ratio": f"({errors} / {total} > 0.02) and ({total} >= 20)",
        "image_429_ratio": f"({limited} / {total} > 0.01) and ({total} >= 20)",
        "image_cpu": f"100 * {cpu_useful} / ({cpu_useful} + {cpu_idle})",
        "image_mem_free_percent": (f"100 * {s['image_mem_available']} / {s['image_mem_total']}"),
        "image_disk_free_percent": f"100 * {s['image_disk_free']} / {s['image_disk_size']}",
        "image_proxy_p95": (f"histogram_quantile(0.95, sum by (le) (rate({duration}[5m])))"),
        "image_proxy_requests_5m": f"sum(increase({s['image_proxy_requests']}[5m]))",
    }


def render(config: dict[str, Any]) -> dict[str, str]:
    import yaml

    validate_config(config)
    s, e = selectors(config), expressions(config)
    image_s, image_e = image_selectors(), image_expressions()
    # Window aggregations explicitly match the native maximum/minimum contracts.
    rule_doc = yaml.safe_load((HERE / "rules.yml").read_text())
    if not config["worker_alerts_enabled"]:
        rule_doc["groups"] = [
            group for group in rule_doc["groups"] if group["name"] != "findme-workers"
        ]
    if not config["image_origin_alerts_enabled"]:
        rule_doc["groups"] = [
            group for group in rule_doc["groups"] if group["name"] != "findme-image-origin"
        ]
    routing = yaml.safe_load((HERE / "alertmanager.yml").read_text())
    dashboard = json.loads((HERE / "dashboard.json").read_text())
    histogram = config["metrics"]["http_duration"]["name"] + "_bucket"
    substitutions = {
        **s,
        **e,
        **image_s,
        **image_e,
        "folder_id": config["folder_id"],
        "channel_name": config["channel_name"] or "__CHANNEL_NAME_REQUIRED__",
        "telegram_channel_name": config["telegram_channel_name"]
        or "__TELEGRAM_CHANNEL_NAME_REQUIRED__",
        "workspace_id": config["workspace_id"] or "__WORKSPACE_ID_REQUIRED__",
        "image_origin_host": config["image_origin_host"],
        "image_cdn_resource": config["image_cdn_resource"],
        "disk_gib": f"{s['disk_free']} / 1073741824",
        "uptime_seconds": f"{s['uptime']} / 1000",
        "http_rate": f"sum(rate({s['http_requests']}[5m]))",
        "http_error_rate": f"sum(rate({s['http_5xx']}[5m]))",
        "http_p50": f"histogram_quantile(0.50, sum by (le) (rate({histogram}[5m])))",
        "http_p95": f"histogram_quantile(0.95, sum by (le) (rate({histogram}[5m])))",
        "worker_pool_universe": (
            'label_replace(vector(1), "pool", "bulk", "", "") or '
            'label_replace(vector(1), "pool", "selfie", "", "")'
        ),
        "worker_queue_fresh": (
            "(worker_pool_queue_observation_available == 1) and on(pool) "
            "((time() - worker_pool_queue_observation_timestamp_seconds) >= 0) and on(pool) "
            "((time() - worker_pool_queue_observation_timestamp_seconds) <= "
            f"{WORKER_SOURCE_MAX_AGE})"
        ),
        "worker_cloud_fresh": (
            "((time() - worker_pool_cloud_observation_timestamp_seconds) >= 0) and on(pool) "
            "((time() - worker_pool_cloud_observation_timestamp_seconds) <= "
            f"{WORKER_SOURCE_MAX_AGE}) "
            "and on(pool) (worker_pool_running_instances >= 0)"
        ),
        "worker_current_nodes": (
            "(worker_node_cloud_observation_timestamp_seconds == on(pool) group_left "
            "worker_pool_cloud_observation_timestamp_seconds)"
        ),
        "worker_publisher_fresh": (
            "((time() - worker_pool_native_publisher_success_timestamp_seconds) >= 0) "
            "and on(pool) "
            "((time() - worker_pool_native_publisher_success_timestamp_seconds) <= "
            f"{WORKER_SOURCE_MAX_AGE})"
        ),
    }
    worker_queue_gate = substitutions["worker_queue_fresh"]
    worker_cloud_gate = substitutions["worker_cloud_fresh"]
    runtime_labels = "pool, instance_id, zone_id"
    worker_runtime_gate = (
        f"({substitutions['worker_current_nodes']}) and on(pool) ({worker_cloud_gate}) "
        f"and on({runtime_labels}) (worker_runtime_scrape_available == 1) "
        f"and on({runtime_labels}) (worker_runtime_observation_fresh == 1) "
        f"and on({runtime_labels}) (worker_runtime_observation_age_seconds >= 0) "
        f"and on({runtime_labels}) "
        "((time() - timestamp(worker_runtime_observation_age_seconds)) >= 0) "
        f"and on({runtime_labels}) "
        "((worker_runtime_observation_age_seconds + time() - "
        "timestamp(worker_runtime_observation_age_seconds)) <= "
        f"{WORKER_SOURCE_MAX_AGE})"
    )
    runtime_rate = (
        "rate(worker_runtime_executions_total[5m]) "
        f"and on({runtime_labels}) ({worker_runtime_gate})"
    )

    def bucket_rate(bound: str) -> str:
        return (
            "sum by (pool, kind, outcome) ("
            "rate(worker_runtime_execution_duration_seconds_bucket"
            f'{{le="{bound}"}}[5m]) and on({runtime_labels}) ({worker_runtime_gate}))'
        )

    def interval_rate(lower: str, upper: str) -> str:
        return f"clamp_min(({bucket_rate(upper)}) - ({bucket_rate(lower)}), 0) * 60"

    substitutions.update(
        {
            "worker_claimable_chart": (f"worker_pool_claimable and on(pool) ({worker_queue_gate})"),
            "worker_oldest_age_chart": (
                f"worker_pool_oldest_claimable_age_seconds and on(pool) ({worker_queue_gate})"
            ),
            "worker_workload_chart": (f"worker_pool_workload and on(pool) ({worker_queue_gate})"),
            "worker_running_chart": (
                f"worker_pool_running_instances and on(pool) ({worker_cloud_gate})"
            ),
            "worker_operation_rate": (f"sum by (pool, kind, outcome) ({runtime_rate}) * 60"),
            "worker_duration_p50": (
                "histogram_quantile(0.50, sum by (pool, kind, outcome, le) ("
                "rate(worker_runtime_execution_duration_seconds_bucket[5m]) "
                f"and on({runtime_labels}) ({worker_runtime_gate})))"
            ),
            "worker_duration_p95": (
                "histogram_quantile(0.95, sum by (pool, kind, outcome, le) ("
                "rate(worker_runtime_execution_duration_seconds_bucket[5m]) "
                f"and on({runtime_labels}) ({worker_runtime_gate})))"
            ),
            "worker_bucket_le_1": f"{bucket_rate('1')} * 60",
            "worker_bucket_1_5": interval_rate("1", "5"),
            "worker_bucket_5_15": interval_rate("5", "15"),
            "worker_bucket_15_60": interval_rate("15", "60"),
            "worker_bucket_60_300": interval_rate("60", "300"),
            "worker_bucket_300_900": interval_rate("300", "900"),
            "worker_bucket_900_1800": interval_rate("900", "1800"),
            "worker_bucket_gt_1800": interval_rate("1800", "+Inf"),
            "accepted_preview_rate": (
                "(rate(findme_accepted_previews_total[5m]) * 60) and on() "
                "((time() - timestamp(findme_accepted_previews_total)) >= 0) and on() "
                "((time() - timestamp(findme_accepted_previews_total)) <= 120)"
            ),
        }
    )

    def substitute(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: substitute(item) for key, item in value.items()}
        if isinstance(value, list):
            return [substitute(item) for item in value]
        if isinstance(value, str):
            return re.sub(
                r"\{\{([a-z_][a-z_0-9]*)\}\}", lambda match: substitutions[match.group(1)], value
            )
        return value

    return {
        "rules.yml": yaml.safe_dump(substitute(rule_doc), sort_keys=False),
        "alertmanager.yml": yaml.safe_dump(substitute(routing), sort_keys=False),
        "dashboard.json": json.dumps(substitute(dashboard), ensure_ascii=False, indent=2) + "\n",
    }


def rule_identities(rendered_rules: str) -> dict[str, list[str]]:
    import yaml

    document = yaml.safe_load(rendered_rules)
    result: dict[str, list[str]] = {}
    for group in document.get("groups", []):
        name = group.get("name")
        alerts = [rule.get("alert") for rule in group.get("rules", [])]
        if (
            not isinstance(name, str)
            or not name
            or name in result
            or any(not isinstance(alert, str) or not alert for alert in alerts)
            or len(alerts) != len(set(alerts))
        ):
            raise ControlError("rendered rule identities invalid")
        result[name] = alerts
    if not result:
        raise ControlError("rendered rule identities invalid")
    return result


def dashboard_request(
    current: dict[str, Any], desired: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    if (
        current.get("folderId") != config["folder_id"]
        or current.get("id") != config["dashboard_id"]
    ):
        raise ControlError("dashboard folder/identity mismatch")
    if not current.get("etag"):
        raise ControlError("dashboard etag missing")
    # Start from the fresh Dashboard; retain every writable nonowned field.
    writable = (
        "name",
        "description",
        "labels",
        "title",
        "widgets",
        "parametrization",
        "etag",
        "managedBy",
        "managedLink",
        "timeline",
        "links",
        "comment",
        "presetItems",
    )
    request = {key: copy.deepcopy(current[key]) for key in writable if key in current}
    request.update(desired)
    request["dashboardId"] = config["dashboard_id"]
    return request


def _vector(response: dict[str, Any]) -> list[dict[str, Any]]:
    if (
        response.get("status") != "success"
        or response.get("data", {}).get("resultType") != "vector"
    ):
        raise ControlError("query calculation failed")
    return response["data"]["result"]


def _fresh_matrix(
    transport: Any,
    query: str,
    *,
    key: str,
    max_age: int,
    now: float | None,
) -> list[tuple[dict[str, Any], float]]:
    response = transport.request("GET", "/api/v1/query?" + urlencode({"query": query}))
    observed_at = time.time() if now is None else now
    data = response.get("data")
    if (
        response.get("status") != "success"
        or not isinstance(data, dict)
        or data.get("resultType") != "matrix"
    ):
        raise ControlError(f"sample query failed: {key}")
    values = data.get("result")
    if not isinstance(values, list) or not values:
        raise ControlError(f"expected sample missing: {key}")
    result = []
    for item in values:
        try:
            points = item["values"]
            metric = item.get("metric", {})
            if not isinstance(metric, dict) or not isinstance(points, list) or not points:
                raise ValueError
            latest = points[-1]
            if not isinstance(latest, list) or len(latest) != 2:
                raise ValueError
            timestamp, number = map(float, latest)
        except (KeyError, TypeError, ValueError):
            raise ControlError(f"sample malformed: {key}") from None
        if (
            not math.isfinite(timestamp)
            or not math.isfinite(number)
            or not 0 <= observed_at - timestamp <= max_age
        ):
            raise ControlError(f"sample stale/nonfinite: {key}")
        result.append((metric, number))
    return result


def _preflight_workers(
    transport: Any,
    *,
    now: float | None,
) -> None:
    metrics = tuple(WORKER_POOL_METRICS.values()) + WORKER_NODE_METRICS
    # One evaluation prevents a collector tick from splitting pool and node membership.
    samples = _fresh_matrix(
        transport,
        f"{{__name__=~{json.dumps('|'.join(metrics))},"
        f"pool=~{json.dumps('|'.join(WORKER_POOLS))}}}[{WORKER_SOURCE_MAX_AGE}s]",
        key="worker_snapshot",
        max_age=WORKER_SOURCE_MAX_AGE,
        now=now,
    )
    snapshot: dict[str, dict[str, list[tuple[dict[str, Any], float]]]] = {
        pool: {metric: [] for metric in metrics} for pool in WORKER_POOLS
    }
    for labels, value in samples:
        pool, metric = labels.get("pool"), labels.get("__name__")
        if pool not in snapshot or metric not in snapshot[pool]:
            raise ControlError("worker snapshot identity invalid")
        snapshot[pool][metric].append((labels, value))
    for pool in WORKER_POOLS:
        values: dict[str, float] = {}
        for key, metric in WORKER_POOL_METRICS.items():
            samples = snapshot[pool][metric]
            if not samples:
                raise ControlError(f"expected sample missing: worker_{pool}_{key}")
            if len(samples) != 1:
                raise ControlError(f"worker sample identity invalid: {pool}_{key}")
            values[key] = samples[0][1]
        observed_at = time.time() if now is None else now
        if values["queue_available"] != 1:
            raise ControlError(f"worker queue observation unavailable: {pool}")
        for key in ("queue_timestamp", "cloud_timestamp", "publisher_timestamp"):
            if not 0 <= observed_at - values[key] <= WORKER_SOURCE_MAX_AGE:
                raise ControlError(f"worker source stale/future: {pool}_{key}")
        if values["running"] not in (0, 1) or values["expected"] not in (0, 1):
            raise ControlError(f"worker capacity invalid: {pool}")
        if any(values[key] < 0 for key in ("claimable", "oldest_age", "workload")):
            raise ControlError(f"worker queue value invalid: {pool}")
        expected = int(values["expected"])
        if expected == 0:
            continue
        identities: set[tuple[str, str]] | None = None
        for metric in WORKER_NODE_METRICS:
            samples = snapshot[pool][metric]
            current = {
                (str(labels.get("instance_id", "")), str(labels.get("zone_id", "")))
                for labels, value in samples
                if metric == "worker_node_cloud_observation_timestamp_seconds" or value in (0, 1)
            }
            if (
                len(samples) != expected
                or len(current) != expected
                or any(not all(identity) for identity in current)
            ):
                raise ControlError(f"worker node diagnostics invalid: {pool}")
            if metric == "worker_node_cloud_observation_timestamp_seconds" and any(
                value != values["cloud_timestamp"] for _, value in samples
            ):
                raise ControlError(f"worker node membership stale: {pool}")
            if identities is not None and current != identities:
                raise ControlError(f"worker node diagnostics inconsistent: {pool}")
            identities = current


def preflight(config: dict[str, Any], transport: Any, *, now: float | None = None) -> None:
    validate_config(config, live=True)
    if not config.get("type_contract_evidence"):
        raise ControlError("metric type contract requires reviewed live evidence")
    s = selectors(config)
    for key, metric in config["metrics"].items():
        selector = s[key]
        if metric["type"] == "histogram":
            selector = metric["name"] + "_count" + selector[len(metric["name"]) :]
        query = f"{selector}[{metric['max_age']}s]"
        _fresh_matrix(
            transport,
            query,
            key=key,
            max_age=metric["max_age"],
            now=now,
        )
    if config["image_origin_alerts_enabled"]:
        image_s = image_selectors()
        for key in (
            "image_cpu_useful",
            "image_cpu_idle",
            "image_mem_available",
            "image_mem_total",
            "image_disk_free",
            "image_disk_size",
            "image_2xx",
            "image_5xx",
            "image_limited",
            "image_auth_rejected",
            "image_proxy_requests",
            "image_proxy_duration_count",
        ):
            _fresh_matrix(
                transport,
                f"{image_s[key]}[120s]",
                key=key,
                max_age=120,
                now=now,
            )
    if config["worker_alerts_enabled"]:
        _preflight_workers(transport, now=now)
    # HTTP zero traffic is valid. Divide only when positive; an absent result is failure.
    for key, query in expressions(config).items():
        values = _vector(transport.request("GET", "/api/v1/query?" + urlencode({"query": query})))
        if not values or any(not math.isfinite(float(item["value"][1])) for item in values):
            raise ControlError(f"expression calculation failed: {key}")
    import yaml

    groups = yaml.safe_load(render(config)["rules.yml"])["groups"]
    for rule in (rule for group in groups for rule in group["rules"]):
        values = _vector(
            transport.request("GET", "/api/v1/query?" + urlencode({"query": rule["expr"]}))
        )
        if any(not math.isfinite(float(item["value"][1])) for item in values):
            raise ControlError("alert expression returned nonfinite values")


def verify_snapshots(
    snapshot: dict[str, Any],
    expected: dict[str, list[str]],
    *,
    now: float | None = None,
    earliest: float = 0,
) -> None:
    now = time.time() if now is None else now
    groups = snapshot.get("snapshotByGroup")
    if not isinstance(groups, dict) or set(groups) != set(expected):
        raise ControlError("rule evaluation snapshot missing, stale or failed")
    entries = [item for group in groups.values() for item in group]
    actual = {
        group: [item.get("record") for item in items]
        for group, items in groups.items()
        if isinstance(items, list)
    }
    if (
        any(set(actual.get(group, [])) != set(names) for group, names in expected.items())
        or any(len(names) != len(set(names)) for names in actual.values())
        or any(
            item.get("state") != "OK"
            or item.get("error")
            or not earliest <= item.get("evaluatedAtTimeEpochMs", 0) / 1000 <= now + 5
            or now - item.get("evaluatedAtTimeEpochMs", 0) / 1000 > 180
            for item in entries
        )
    ):
        raise ControlError("rule evaluation snapshot missing, stale or failed")


def check(config: dict[str, Any], transport: Any, *, now: float | None = None) -> dict[str, Any]:
    preflight(config, transport, now=now)
    package = render(config)
    current = transport.dashboard_get(config["dashboard_id"])
    dashboard_request(current, json.loads(package["dashboard.json"]), config)
    rules = transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES)
    if rules.get("content"):
        expected = rule_identities(package["rules.yml"])
        verify_snapshots(
            transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES + "/snapshots"),
            expected,
            now=now,
        )
    return {
        "dashboard_matches": all(
            current.get(key) == value
            for key, value in json.loads(package["dashboard.json"]).items()
        ),
        "rules_match": rules.get("content")
        == base64.b64encode(package["rules.yml"].encode()).decode(),
        "routing_drift": "unverified: supported GET Alertmanager contract unavailable",
    }


def dedicated_workspace(transport: Any) -> None:
    response = transport.request("GET", "/extensions/v1/rules")
    files = response.get("files")
    if not isinstance(files, list) or any(name != OWNED_RULES for name in files):
        raise ControlError("routing replacement requires a dedicated FindMe workspace")


def apply_routing(config: dict[str, Any], transport: Any) -> None:
    validate_config(config, live=True)
    dedicated_workspace(transport)
    routing = render(config)["alertmanager.yml"]
    transport.request(
        "PUT",
        "/extensions/v1/alertmanager",
        {"content": base64.b64encode(routing.encode()).decode()},
    )


def restore(
    config: dict[str, Any], transport: Any, backup: Path, *, routing_file: Path | None
) -> None:
    validate_config(config, live=True)
    manifest = load_config(backup / "target.json")
    if any(
        config[key] != manifest.get(key) for key in ("folder_id", "workspace_id", "dashboard_id")
    ):
        raise ControlError("backup target identity mismatch")
    saved = load_config(backup / "dashboard.json")
    current = transport.dashboard_get(config["dashboard_id"])
    desired = {key: saved.get(key, "" if key == "title" else []) for key in ("title", "widgets")}
    request = dashboard_request(current, desired, config)
    previous_rules = load_config(backup / "rules.json")
    if routing_file:
        dedicated_workspace(transport)
        transport.request(
            "PUT",
            "/extensions/v1/alertmanager",
            {"content": base64.b64encode(routing_file.read_bytes()).decode()},
        )
    if previous_rules.get("absent"):
        transport.request("DELETE", "/extensions/v1/rules/" + OWNED_RULES)
    else:
        transport.request(
            "PUT",
            "/extensions/v1/rules",
            {"name": OWNED_RULES, "content": previous_rules["content"]},
        )
    restored_rules = transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES)
    if (previous_rules.get("absent") and not restored_rules.get("absent")) or (
        not previous_rules.get("absent")
        and restored_rules.get("content") != previous_rules.get("content")
    ):
        raise ControlError("rules restore read-back mismatch")
    transport.dashboard_update(request)
    result = transport.dashboard_get(config["dashboard_id"])
    if any(result.get(key) != value for key, value in desired.items()):
        raise ControlError("dashboard restore read-back mismatch")


def apply(
    config: dict[str, Any], transport: Any, backup: Path, *, now: float | None = None
) -> None:
    dedicated_workspace(transport)
    preflight(config, transport, now=now)
    package = render(config)
    current = transport.dashboard_get(config["dashboard_id"])
    desired = json.loads(package["dashboard.json"])
    request = dashboard_request(current, desired, config)
    previous_rules = transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES)
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, value in [
        ("dashboard.json", current),
        ("rules.json", previous_rules),
        (
            "target.json",
            {key: config[key] for key in ("folder_id", "workspace_id", "dashboard_id")},
        ),
    ]:
        (backup / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    (backup / "routing-status.txt").write_text(
        "No server-side routing backup: restore by applying a known Git revision.\n"
    )
    transport.request(
        "PUT",
        "/extensions/v1/alertmanager",
        {"content": base64.b64encode(package["alertmanager.yml"].encode()).decode()},
    )
    rules_body = {
        "name": OWNED_RULES,
        "content": base64.b64encode(package["rules.yml"].encode()).decode(),
    }
    applied_at = time.time() if now is None else now
    transport.request("PUT", "/extensions/v1/rules", rules_body)
    read_rules = transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES)
    if read_rules.get("content") != rules_body["content"]:
        raise ControlError("rules read-back mismatch")
    transport.wait_rules_evaluation(
        OWNED_RULES,
        expected=rule_identities(package["rules.yml"]),
        earliest=applied_at,
    )
    transport.dashboard_update(request)
    readback = transport.dashboard_get(config["dashboard_id"])
    if readback.get("folderId") != config["folder_id"] or any(
        readback.get(key) != value for key, value in desired.items()
    ):
        raise ControlError("dashboard read-back mismatch; restore saved snapshot")


class CloudTransport:
    def __init__(self, config: dict[str, Any], token: str):
        import yandexcloud
        from yandex.cloud.monitoring.v3.dashboard_service_pb2_grpc import DashboardServiceStub

        self.config, self.token = config, token
        self.sdk = yandexcloud.SDK(
            iam_token=token, endpoints={"monitoring": "monitoring.api.cloud.yandex.net:443"}
        )
        # Pinned SDK 0.408.0 ships Monitoring stubs but omits their client registry entry.
        # Its channel factory retains SDK TLS/IAM handling without patching that registry.
        self.dashboard = DashboardServiceStub(self.sdk._channels.channel("monitoring"))

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = API + "/prometheus/workspaces/" + self.config["workspace_id"] + path
        request = Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            if (
                error.code == 404
                and method == "GET"
                and path
                in {
                    "/extensions/v1/rules/" + OWNED_RULES,
                    "/extensions/v1/rules/findme-worker-activation-drill.yml",
                }
            ):
                return {"content": "", "absent": True}
            raise ControlError(f"Monitoring {method} failed (HTTP {error.code})") from None
        except Exception:
            raise ControlError(f"Monitoring {method} failed") from None

    def wait_rules_evaluation(
        self, name: str, *, expected: dict[str, list[str]], earliest: float
    ) -> None:
        deadline = time.monotonic() + 120
        while True:
            snapshot = self.request("GET", "/extensions/v1/rules/" + name + "/snapshots")
            try:
                verify_snapshots(snapshot, expected, earliest=earliest)
                return
            except ControlError:
                entries = [
                    item for group in snapshot.get("snapshotByGroup", {}).values() for item in group
                ]
                if (
                    any(
                        item.get("state") not in ("OK", "NOT_EVALUATED_YET") or item.get("error")
                        for item in entries
                    )
                    or time.monotonic() >= deadline
                ):
                    raise ControlError(
                        "rule evaluation failed or timed out; inspect saved backup"
                    ) from None
            time.sleep(5)

    def dashboard_get(self, dashboard_id: str) -> dict[str, Any]:
        from google.protobuf.json_format import MessageToDict
        from yandex.cloud.monitoring.v3.dashboard_service_pb2 import GetDashboardRequest

        try:
            return MessageToDict(
                self.dashboard.Get(GetDashboardRequest(dashboard_id=dashboard_id), timeout=30)
            )
        except Exception:
            raise ControlError("DashboardService Get failed") from None

    def dashboard_update(self, request: dict[str, Any]) -> None:
        from google.protobuf.json_format import ParseDict
        from yandex.cloud.monitoring.v3.dashboard_service_pb2 import UpdateDashboardRequest

        try:
            operation = self.dashboard.Update(
                ParseDict(request, UpdateDashboardRequest()), timeout=30
            )
            # Monitoring Update is synchronous; its operation cannot be polled globally.
            if not operation.done or operation.HasField("error"):
                raise ControlError("dashboard operation incomplete or failed")
        except Exception:
            raise ControlError("DashboardService Update failed; inspect saved backup") from None


def identity(mode: str, oidc_path: Path | None = None) -> str:
    # Reuse the repository's validated short-lived identity exchange, without Lockbox consumers.
    spec = importlib.util.spec_from_file_location(
        "monitoring_identity", ROOT / "scripts/run-with-environment-secrets.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        if mode == "github-oidc":
            if oidc_path is None:
                raise ControlError("github-oidc requires --oidc-config")
            return str(module._github_identity(load_config(oidc_path), os.environ))
        return str(module._yc_identity())
    except Exception:
        raise ControlError(
            "short-lived identity failed; verify protected environment and IAM"
        ) from None


def _validate_dashboard(package: dict[str, str], config: dict[str, Any]) -> None:
    dashboard = json.loads(package["dashboard.json"])
    for widget in dashboard["widgets"]:
        chart = widget["multiSourceChart"]
        sources = {}
        for item in chart["dataSources"]:
            kinds = [kind for kind in ("prometheus", "monitoring") if kind + "DataSource" in item]
            if len(kinds) != 1:
                raise ControlError("dashboard source kind invalid")
            kind = kinds[0]
            source = item[kind + "DataSource"]
            source_id = source.get("id")
            if not source_id or source_id in sources:
                raise ControlError("dashboard source identity invalid")
            sources[source_id] = kind
            if kind == "prometheus" and int(source.get("step", 0)) <= 0:
                raise ControlError("dashboard Prometheus grid step must be positive")
        for item in chart["targets"]:
            kinds = [kind for kind in ("prometheus", "monitoring") if kind + "Target" in item]
            if len(kinds) != 1:
                raise ControlError("dashboard target kind invalid")
            kind = kinds[0]
            target = item[kind + "Target"]
            query = target.get("query")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", target.get("name", "")):
                raise ControlError(
                    "dashboard target name must use Latin letters/digits and start with a letter"
                )
            if (
                sources.get(target.get("dataSourceId")) != kind
                or not query
                or "{{" in query
                or (kind == "prometheus" and not target.get("workspaceId"))
            ):
                raise ControlError("dashboard target reference/workspace/query invalid")


def validate_dashboard_queries(package: dict[str, str], output: Path, promtool: str) -> None:
    import yaml

    dashboard = json.loads(package["dashboard.json"])
    queries: list[tuple[str, str]] = []
    for widget in dashboard["widgets"]:
        for item in widget["multiSourceChart"]["targets"]:
            if "prometheusTarget" in item:
                target = item["prometheusTarget"]
                queries.append((target["name"], target["query"]))
    rules_path = output / "dashboard-query-rules.yml"
    rules_path.write_text(
        yaml.safe_dump(
            {
                "groups": [
                    {
                        "name": "findme-dashboard-queries",
                        "rules": [
                            {"record": f"findme_dashboard_query_{index}", "expr": query}
                            for index, (_, query) in enumerate(queries)
                        ],
                    }
                ]
            },
            sort_keys=False,
        )
    )
    subprocess.run([promtool, "check", "rules", str(rules_path)], check=True)

    by_name = {name: query for name, query in queries}
    tests = load_config(HERE / "dashboard-query-tests.json")
    for case in tests["tests"]:
        for expression in case["promql_expr_test"]:
            try:
                expression["expr"] = by_name[expression["expr"]]
            except KeyError as error:
                raise ControlError("dashboard behavior fixture target missing") from error
    tests["rule_files"] = [str(rules_path.resolve())]
    tests_path = output / "dashboard-query-tests.yml"
    tests_path.write_text(yaml.safe_dump(tests, sort_keys=False))
    subprocess.run([promtool, "test", "rules", str(tests_path)], check=True)


def validate_prometheus_profiles(config: dict[str, Any], output: Path, promtool: str) -> None:
    version = subprocess.run([promtool, "--version"], check=True, capture_output=True, text=True)
    if f"version {PROMETHEUS_VERSION}" not in version.stdout + version.stderr:
        raise ControlError(f"promtool must be {PROMETHEUS_VERSION}")
    import yaml

    output.mkdir(parents=True, exist_ok=True)
    validate_dashboard_queries(render(config), output, promtool)

    for enabled in (False, True):
        profile = copy.deepcopy(config)
        profile["worker_alerts_enabled"] = enabled
        profile_output = (
            output
            if enabled == config["worker_alerts_enabled"]
            else output / ("worker-alerts-enabled" if enabled else "worker-alerts-disabled")
        )
        package = render(profile)
        profile_output.mkdir(parents=True, exist_ok=True)
        for name, content in package.items():
            (profile_output / name).write_text(content)
        rules_path = profile_output / "rules.yml"
        subprocess.run([promtool, "check", "rules", str(rules_path)], check=True)
        tests = load_config(HERE / "rule-tests.json")
        if enabled:
            tests["tests"].extend(load_config(HERE / "worker-rule-tests.json")["tests"])
        tests["rule_files"] = [str(rules_path.resolve())]
        tests_path = profile_output / "rule-tests.yml"
        tests_path.write_text(yaml.safe_dump(tests, sort_keys=False))
        subprocess.run([promtool, "test", "rules", str(tests_path)], check=True)


def validate_package(config: dict[str, Any], output: Path, promtool: str) -> None:
    from google.protobuf.json_format import ParseDict
    from yandex.cloud.monitoring.v3.dashboard_service_pb2 import UpdateDashboardRequest

    package = render(config)
    _validate_dashboard(package, config)
    ParseDict(
        {"dashboardId": config["dashboard_id"], **json.loads(package["dashboard.json"])},
        UpdateDashboardRequest(),
    )
    validate_prometheus_profiles(config, output, promtool)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("render", "validate", "check", "apply", "apply-routing", "restore")
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/tmp/findme-monitoring-render"))
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--routing-file", type=Path)
    parser.add_argument("--promtool", default="promtool")
    parser.add_argument("--identity", choices=("yc", "github-oidc"), default="yc")
    parser.add_argument("--oidc-config", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.command == "validate":
            validate_package(config, args.output, args.promtool)
        elif args.command == "render":
            args.output.mkdir(parents=True, exist_ok=True)
            for name, content in render(config).items():
                (args.output / name).write_text(content)
        else:
            validate_config(config, live=True)
            transport = CloudTransport(config, identity(args.identity, args.oidc_config))
            if args.command == "check":
                print(json.dumps(check(config, transport), indent=2))
            elif args.command == "apply-routing":
                apply_routing(config, transport)
                print("Applied Alertmanager routing only; verify delivery with a live drill.")
            elif args.command == "restore":
                if args.backup is None:
                    raise ControlError("restore requires --backup")
                restore(config, transport, args.backup, routing_file=args.routing_file)
                print("Restored saved owned objects; routing requires --routing-file.")
            else:
                if args.backup is None:
                    raise ControlError("apply requires a new --backup directory")
                apply(config, transport, args.backup)
                print(
                    "Applied owned rules and dashboard; routing drift remains unverified. "
                    "Live email acceptance is pending."
                )
    except (ControlError, OSError, ValueError, subprocess.CalledProcessError):
        # Never echo exception details from SDK, HTTP, JSON or subprocess credentials.
        error = sys.exc_info()[1]
        print(
            str(error)
            if isinstance(error, ControlError)
            else "monitoring command failed; check configuration/tooling",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
