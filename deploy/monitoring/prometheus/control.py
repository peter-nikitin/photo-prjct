#!/usr/bin/env python3
"""Yandex Monitoring dashboard transport and validation."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

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


class CloudTransport:
    def __init__(self, config: dict[str, Any], token: str):
        import requests
        import yandexcloud
        from yandex.cloud.monitoring.v3.dashboard_service_pb2_grpc import DashboardServiceStub

        self.config, self.token = config, token
        self.session = requests.Session()
        self.sdk = yandexcloud.SDK(
            iam_token=token, endpoints={"monitoring": "monitoring.api.cloud.yandex.net:443"}
        )
        # Pinned SDK 0.408.0 ships Monitoring stubs but omits their client registry entry.
        # Its channel factory retains SDK TLS/IAM handling without patching that registry.
        self.dashboard = DashboardServiceStub(self.sdk._channels.channel("monitoring"))

    def read_samples(
        self, selectors: list[dict[str, str]], *, now: float | None = None
    ) -> list[list[list[tuple[float, float]]]]:
        import remote_pb2
        import requests
        import snappy
        import types_pb2

        observed = time.time() if now is None else now
        query = remote_pb2.ReadRequest()
        for selector in selectors:
            item = query.queries.add(
                start_timestamp_ms=int((observed - 180) * 1000),
                end_timestamp_ms=int(observed * 1000),
            )
            for name, value in selector.items():
                item.matchers.add(type=types_pb2.LabelMatcher.EQ, name=name, value=value)
        url = API + "/prometheus/workspaces/" + self.config["workspace_id"] + "/api/v1/read"
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/x-protobuf",
            "Content-Encoding": "snappy",
            "X-Prometheus-Remote-Read-Version": "0.1.0",
        }
        for attempt in range(3):
            try:
                response = self.session.post(
                    url,
                    data=snappy.compress(query.SerializeToString()),
                    headers=headers,
                    timeout=30,
                )
                if 500 <= response.status_code < 600 and attempt < 2:
                    time.sleep(1)
                    continue
                if response.status_code != 200:
                    raise ControlError(f"Monitoring read failed (HTTP {response.status_code})")
                parsed = remote_pb2.ReadResponse.FromString(snappy.decompress(response.content))
                if len(parsed.results) != len(selectors):
                    raise ControlError("Monitoring read result count mismatch")
                return [
                    [
                        [(point.timestamp / 1000, point.value) for point in series.samples]
                        for series in result.timeseries
                    ]
                    for result in parsed.results
                ]
            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise ControlError("Monitoring read timed out") from None
            except ControlError:
                raise
            except Exception:
                raise ControlError("Monitoring read failed") from None
        raise AssertionError("unreachable Monitoring read retry state")

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
