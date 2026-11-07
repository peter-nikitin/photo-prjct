import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_prometheus_control():
    path = ROOT / "deploy/monitoring/prometheus/control.py"
    spec = importlib.util.spec_from_file_location("deployment_monitoring_control", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dashboard_queries_do_not_include_private_identity_labels() -> None:
    import re

    dashboard = json.loads((ROOT / "deploy/monitoring/dashboard.json").read_text())
    queries = [
        target["query"]
        for widget in dashboard["widgets"]
        if "chart" in widget
        for target in widget["chart"]["queries"]["targets"]
    ]
    assert queries
    for query in queries:
        for label in (
            "email",
            "token",
            "user_id",
            "photo_id",
            "order_id",
            "payment_id",
            "session_id",
        ):
            assert not re.search(rf"\b{label}\s*(?:!?=|!~)", query)


def test_worker_alert_toggle_preserves_unrelated_rendered_rules() -> None:
    control = load_prometheus_control()
    config = control.load_config()
    enabled = yaml.safe_load(control.render({**config, "worker_alerts_enabled": True})["rules.yml"])
    disabled = yaml.safe_load(
        control.render({**config, "worker_alerts_enabled": False})["rules.yml"]
    )
    enabled_groups = {group["name"]: group for group in enabled["groups"]}
    disabled_groups = {group["name"]: group for group in disabled["groups"]}
    assert disabled_groups.keys() < enabled_groups.keys()
    for name, group in disabled_groups.items():
        assert enabled_groups[name] == group
    assert all(
        group["rules"] for name, group in enabled_groups.items() if name not in disabled_groups
    )
