#!/usr/bin/env python3
"""Reconcile the existing dashboard independently of metric availability."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


control = _load("monitoring_control", HERE / "control.py")
queue_panels = _load("monitoring_queue_panels", HERE / "queue_panels.py")
catalog = _load("monitoring_queue_catalog", HERE.parents[2] / "src/backend/config/queue_catalog.py")
CONFIG = {key: control.load_config()[key] for key in ("folder_id", "workspace_id", "dashboard_id")}


layout = _load("monitoring_dashboard_layout", HERE / "dashboard_layout.py")


def dashboards() -> dict[str, dict]:
    return layout.render(CONFIG, catalog.QUEUES, queue_panels.panels)


def reconcile(transport, backup: Path) -> None:
    desired = dashboards()
    child_names = {value["name"] for key, value in desired.items() if key != "overview"}
    existing = {
        item["name"]: item["id"]
        for item in transport.dashboard_list()
        if item.get("name") in child_names
    }
    # Publish destinations first; the existing landing page is replaced last.
    for key in (*[key for key in desired if key != "overview"], "overview"):
        content = desired[key]
        dashboard_id = (
            CONFIG["dashboard_id"] if key == "overview" else existing.get(content["name"])
        )
        if dashboard_id:
            current = transport.dashboard_get(dashboard_id)
            config = {**CONFIG, "dashboard_id": dashboard_id}
            request = control.dashboard_request(current, content, config)
            if all(current.get(field) == value for field, value in content.items()):
                continue
            backup.mkdir(mode=0o700, parents=True, exist_ok=True)
            (backup / f"{key}.json").write_text(
                json.dumps(current, ensure_ascii=False, indent=2) + "\n"
            )
            transport.dashboard_update(request)
        else:
            dashboard_id = transport.dashboard_create({"folderId": CONFIG["folder_id"], **content})
        readback = transport.dashboard_get(dashboard_id)
        if readback.get("folderId") != CONFIG["folder_id"] or any(
            readback.get(field) != value for field, value in content.items()
        ):
            raise control.ControlError("dashboard read-back mismatch; inspect backup")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--promtool")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--oidc-config", type=Path)
    args = parser.parse_args()
    try:
        if args.validate:
            from google.protobuf.json_format import MessageToDict, ParseDict
            from yandex.cloud.monitoring.v3.dashboard_service_pb2 import UpdateDashboardRequest

            for key, desired in dashboards().items():
                control._validate_dashboard({"dashboard.json": json.dumps(desired)}, CONFIG)
                parsed = ParseDict(
                    {"dashboardId": CONFIG["dashboard_id"], **desired}, UpdateDashboardRequest()
                )
                canonical = MessageToDict(parsed)
                if any(canonical.get(key) != value for key, value in desired.items()):
                    raise control.ControlError("dashboard must use canonical SDK representation")
                if args.promtool:
                    import yaml

                    charts = control._dashboard_charts(desired)
                    defaults = control.dashboard_parameter_defaults(desired)
                    rules = [
                        {
                            "record": f"findme_dashboard_query_{key}_{i}_{j}",
                            "expr": control.dashboard_validation_query(
                                target["prometheusTarget"]["query"], defaults
                            ),
                        }
                        for i, chart in enumerate(charts)
                        for j, target in enumerate(chart["targets"])
                        if "prometheusTarget" in target
                    ]
                    for parameter in desired.get("parametrization", {}).get("parameters", []):
                        matches = parameter.get("query", {}).get("prometheus", {}).get("match", [])
                        for i, match in enumerate(matches):
                            rules.append(
                                {
                                    "record": f"findme_dashboard_parameter_{parameter['name']}_{i}",
                                    "expr": match,
                                }
                            )
                    with tempfile.TemporaryDirectory(
                        prefix=".monitoring-queries-", dir=Path.cwd()
                    ) as directory:
                        path = Path(directory) / "queries.yml"
                        path.write_text(
                            yaml.safe_dump({"groups": [{"name": "dashboard", "rules": rules}]})
                        )
                        subprocess.run([args.promtool, "check", "rules", str(path)], check=True)
            return 0
        if args.backup is None or args.oidc_config is None:
            parser.error("--backup and --oidc-config are required for reconciliation")
        transport = control.CloudTransport(
            CONFIG, control.identity("github-oidc", args.oidc_config)
        )
        reconcile(transport, args.backup)
        return 0
    except (control.ControlError, OSError, ValueError, subprocess.CalledProcessError):
        print("Dashboard reconciliation failed; inspect configuration and backup", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
