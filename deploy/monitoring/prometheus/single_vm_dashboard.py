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
spec = importlib.util.spec_from_file_location("monitoring_control", HERE / "control.py")
assert spec and spec.loader
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
CONFIG = {key: control.load_config()[key] for key in ("folder_id", "workspace_id", "dashboard_id")}


def dashboard() -> dict:
    content = (HERE / "single-vm-dashboard.json").read_text()
    return json.loads(content.replace("{{workspace_id}}", CONFIG["workspace_id"]))


def reconcile(transport, backup: Path) -> None:
    desired = dashboard()
    current = transport.dashboard_get(CONFIG["dashboard_id"])
    request = control.dashboard_request(current, desired, CONFIG)
    if all(current.get(key) == value for key, value in desired.items()):
        return
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    (backup / "dashboard.json").write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    transport.dashboard_update(request)
    readback = transport.dashboard_get(CONFIG["dashboard_id"])
    if readback.get("folderId") != CONFIG["folder_id"] or any(
        readback.get(key) != value for key, value in desired.items()
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

            desired = dashboard()
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
                rules = [
                    {
                        "record": f"findme_dashboard_query_{i}_{j}",
                        "expr": target["prometheusTarget"]["query"],
                    }
                    for i, chart in enumerate(charts)
                    for j, target in enumerate(chart["targets"])
                ]
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
