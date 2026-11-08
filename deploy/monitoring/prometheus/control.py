#!/usr/bin/env python3
"""Yandex Monitoring dashboard transport and validation."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
API = "https://monitoring.api.cloud.yandex.net"


class ControlError(Exception):
    """Safe operator error: never includes transport response bodies or credentials."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or HERE / "environment.json").read_text())


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
        for attempt in range(3 if method == "GET" else 1):
            try:
                with urlopen(request, timeout=30) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except HTTPError as error:
                raise ControlError(f"Monitoring {method} failed (HTTP {error.code})") from None
            except (TimeoutError, URLError) as error:
                timed_out = isinstance(error, TimeoutError) or isinstance(
                    error.reason, TimeoutError
                )
                if timed_out and method == "GET" and attempt < 2:
                    time.sleep(1)
                    continue
                raise ControlError(f"Monitoring {method} failed") from None
            except Exception:
                raise ControlError(f"Monitoring {method} failed") from None
        raise AssertionError("unreachable Monitoring request retry state")

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


def _dashboard_charts(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    charts = []
    group_ids = set()
    chart_ids = set()
    for widget in dashboard["widgets"]:
        group = widget.get("group")
        if not group or not group.get("id") or not group.get("title"):
            raise ControlError("dashboard group invalid")
        if group["id"] in group_ids:
            raise ControlError("dashboard group identity duplicated")
        group_ids.add(group["id"])
        for child in group.get("widgets", []):
            chart = child.get("multiSourceChart")
            if not child.get("id") or not child.get("position") or not chart:
                raise ControlError("dashboard child chart invalid")
            if child["id"] in chart_ids or child["id"] != chart.get("id"):
                raise ControlError("dashboard chart identity invalid")
            chart_ids.add(child["id"])
            charts.append(chart)
    return charts


def _validate_dashboard(package: dict[str, str], config: dict[str, Any]) -> None:
    dashboard = json.loads(package["dashboard.json"])
    for chart in _dashboard_charts(dashboard):
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
