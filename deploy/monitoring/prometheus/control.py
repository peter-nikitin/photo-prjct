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


class ControlError(Exception):
    """Safe operator error: never includes transport response bodies or credentials."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or HERE / "environment.json").read_text())


def validate_config(config: dict[str, Any], *, live: bool = False) -> None:
    for field in ("folder_id", "dashboard_id", "channel_id") + (
        ("workspace_id", "channel_name") if live else ()
    ):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ControlError(f"activation requires {field}")
    for field in ("folder_id", "dashboard_id", "workspace_id"):
        if config.get(field) and not re.fullmatch(r"[a-zA-Z0-9_-]+", config[field]):
            raise ControlError(f"invalid {field}")
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
    result = {
        key: value["name"]
        + "{"
        + ",".join(f"{name}={json.dumps(label)}" for name, label in sorted(value["labels"].items()))
        + "}"
        for key, value in config["metrics"].items()
    }

    requests = result["http_requests"]
    comma = "," if requests[-2] != "{" else ""
    result["http_5xx"] = requests[:-1] + comma + 'status_class="5xx"}'
    return result


def expressions(config: dict[str, Any]) -> dict[str, str]:
    s = selectors(config)
    return {
        "public": f"max_over_time({s['public_success']}[10m])",
        "tls": f"min_over_time({s['tls_days']}[5m])",
        "disk_percent": f"100 * {s['disk_free']} / {s['disk_size']}",
        "disk_bytes": s["disk_free"],
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


def render(config: dict[str, Any]) -> dict[str, str]:
    import yaml

    validate_config(config)
    s, e = selectors(config), expressions(config)
    # Window aggregations explicitly match the native maximum/minimum contracts.
    rule_doc = yaml.safe_load((HERE / "rules.yml").read_text())
    routing = yaml.safe_load((HERE / "alertmanager.yml").read_text())
    dashboard = json.loads((HERE / "dashboard.json").read_text())
    histogram = config["metrics"]["http_duration"]["name"] + "_bucket"
    substitutions = {
        **s,
        **e,
        "channel_name": config["channel_name"] or "__CHANNEL_NAME_REQUIRED__",
        "workspace_id": config["workspace_id"] or "__WORKSPACE_ID_REQUIRED__",
        "disk_gib": f"{s['disk_free']} / 1073741824",
        "uptime_seconds": f"{s['uptime']} / 1000",
        "http_rate": f"sum(rate({s['http_requests']}[5m]))",
        "http_error_rate": f"sum(rate({s['http_5xx']}[5m]))",
        "http_p50": f"histogram_quantile(0.50, sum by (le) (rate({histogram}[5m])))",
        "http_p95": f"histogram_quantile(0.95, sum by (le) (rate({histogram}[5m])))",
    }

    def substitute(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: substitute(item) for key, item in value.items()}
        if isinstance(value, list):
            return [substitute(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"\{\{([a-z_]+)\}\}", lambda match: substitutions[match.group(1)], value)
        return value

    return {
        "rules.yml": yaml.safe_dump(substitute(rule_doc), sort_keys=False),
        "alertmanager.yml": yaml.safe_dump(substitute(routing), sort_keys=False),
        "dashboard.json": json.dumps(substitute(dashboard), ensure_ascii=False, indent=2) + "\n",
    }


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


def preflight(config: dict[str, Any], transport: Any, *, now: float | None = None) -> None:
    validate_config(config, live=True)
    if not config.get("type_contract_evidence"):
        raise ControlError("metric type contract requires reviewed live evidence")
    s = selectors(config)
    for key, metric in config["metrics"].items():
        selector = s[key]
        if metric["type"] == "histogram":
            selector = metric["name"] + "_count" + selector[len(metric["name"]) :]
        for query in (selector, f"timestamp({selector})"):
            values = _vector(
                transport.request("GET", "/api/v1/query?" + urlencode({"query": query}))
            )
            observed_at = time.time() if now is None else now
            if not values:
                raise ControlError(f"expected sample missing: {key}")
            for item in values:
                number = float(item["value"][1])
                if not math.isfinite(number) or (
                    query.startswith("timestamp(")
                    and not 0 <= observed_at - number <= metric["max_age"]
                ):
                    raise ControlError(f"sample stale/nonfinite: {key}")
    # HTTP zero traffic is valid. Divide only when positive; an absent result is failure.
    for key, query in expressions(config).items():
        values = _vector(transport.request("GET", "/api/v1/query?" + urlencode({"query": query})))
        if not values or any(not math.isfinite(float(item["value"][1])) for item in values):
            raise ControlError(f"expression calculation failed: {key}")
    import yaml

    rules = yaml.safe_load(render(config)["rules.yml"])["groups"][0]["rules"]
    for rule in rules:
        values = _vector(
            transport.request("GET", "/api/v1/query?" + urlencode({"query": rule["expr"]}))
        )
        if any(not math.isfinite(float(item["value"][1])) for item in values):
            raise ControlError("alert expression returned nonfinite values")


def verify_snapshots(
    snapshot: dict[str, Any], *, now: float | None = None, earliest: float = 0
) -> None:
    now = time.time() if now is None else now
    entries = [item for group in snapshot.get("snapshotByGroup", {}).values() for item in group]
    if len(entries) != 11 or any(
        item.get("state") != "OK"
        or item.get("error")
        or not earliest <= item.get("evaluatedAtTimeEpochMs", 0) / 1000 <= now + 5
        or now - item.get("evaluatedAtTimeEpochMs", 0) / 1000 > 180
        for item in entries
    ):
        raise ControlError("rule evaluation snapshot missing, stale or failed")


def check(config: dict[str, Any], transport: Any, *, now: float | None = None) -> dict[str, Any]:
    preflight(config, transport, now=now)
    package = render(config)
    current = transport.dashboard_get(config["dashboard_id"])
    dashboard_request(current, json.loads(package["dashboard.json"]), config)
    rules = transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES)
    if rules.get("content"):
        verify_snapshots(
            transport.request("GET", "/extensions/v1/rules/" + OWNED_RULES + "/snapshots"), now=now
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
    transport.wait_rules_evaluation(OWNED_RULES, earliest=applied_at)
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
        self.dashboard = self.sdk.client(DashboardServiceStub)

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
                and path == "/extensions/v1/rules/" + OWNED_RULES
            ):
                return {"content": "", "absent": True}
            raise ControlError(f"Monitoring {method} failed (HTTP {error.code})") from None
        except Exception:
            raise ControlError(f"Monitoring {method} failed") from None

    def wait_rules_evaluation(self, name: str, *, earliest: float) -> None:
        deadline = time.monotonic() + 120
        while True:
            snapshot = self.request("GET", "/extensions/v1/rules/" + name + "/snapshots")
            try:
                verify_snapshots(snapshot, earliest=earliest)
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
        from yandex.cloud.monitoring.v3.dashboard_pb2 import Dashboard
        from yandex.cloud.monitoring.v3.dashboard_service_pb2 import UpdateDashboardRequest
        from yandexcloud.operations import OperationError

        try:
            operation = self.dashboard.Update(
                ParseDict(request, UpdateDashboardRequest()), timeout=30
            )
            result = self.sdk.wait_operation_and_get_result(
                operation, response_type=Dashboard, timeout=120
            )
            if isinstance(result, OperationError):
                raise ControlError("dashboard operation failed")
        except Exception:
            raise ControlError(
                "DashboardService Update/wait failed; inspect saved backup"
            ) from None


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


def validate_package(config: dict[str, Any], output: Path, promtool: str) -> None:
    from google.protobuf.json_format import ParseDict
    from yandex.cloud.monitoring.v3.dashboard_service_pb2 import UpdateDashboardRequest

    package = render(config)
    dashboard = json.loads(package["dashboard.json"])
    for widget in dashboard["widgets"]:
        chart = widget["multiSourceChart"]
        sources = {item["prometheusDataSource"]["id"] for item in chart["dataSources"]}
        if any(
            int(item["prometheusDataSource"].get("step", 0)) <= 0 for item in chart["dataSources"]
        ):
            raise ControlError("dashboard Prometheus grid step must be positive")
        if any(
            not target["prometheusTarget"]["workspaceId"]
            or target["prometheusTarget"]["dataSourceId"] not in sources
            or not target["prometheusTarget"]["query"]
            for target in chart["targets"]
        ):
            raise ControlError("dashboard target reference/workspace/query invalid")
    ParseDict(
        {"dashboardId": config["dashboard_id"], **json.loads(package["dashboard.json"])},
        UpdateDashboardRequest(),
    )
    output.mkdir(parents=True, exist_ok=True)
    for name, content in package.items():
        (output / name).write_text(content)
    version = subprocess.run([promtool, "--version"], check=True, capture_output=True, text=True)
    if f"version {PROMETHEUS_VERSION}" not in version.stdout + version.stderr:
        raise ControlError(f"promtool must be {PROMETHEUS_VERSION}")
    subprocess.run([promtool, "check", "rules", str(output / "rules.yml")], check=True)
    tests = load_config(HERE / "rule-tests.json")
    tests["rule_files"] = [str((output / "rules.yml").resolve())]
    import yaml

    (output / "rule-tests.yml").write_text(yaml.safe_dump(tests, sort_keys=False))
    subprocess.run([promtool, "test", "rules", str(output / "rule-tests.yml")], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("render", "validate", "check", "apply", "restore"))
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
