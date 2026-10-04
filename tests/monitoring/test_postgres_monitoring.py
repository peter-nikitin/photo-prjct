import importlib.util
import json
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[2] / "deploy/monitoring/prometheus"


def control():
    spec = importlib.util.spec_from_file_location(
        "postgres_monitoring_control", HERE / "control.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postgres_group_uses_verified_sources_and_no_synthetic_health():
    ctl = control()
    package = ctl.render(ctl.load_config())
    dashboard = json.loads(package["dashboard.json"])
    groups = [widget["group"] for widget in dashboard["widgets"]]
    postgres = [group for group in groups if group["title"] == "PostgreSQL"]
    assert len(postgres) == 1
    queries = [
        target["prometheusTarget"]["query"]
        for chart in postgres[0]["widgets"]
        for target in chart["multiSourceChart"]["targets"]
    ]
    assert any("findme_db_usable" in query and "timestamp" in query for query in queries)
    assert any('pg_up{job="findme-postgres"}' in query for query in queries)
    assert any("rate(findme_db_wal_bytes_total[5m])" in query for query in queries)
    assert any("pg_database_size_bytes" in query and 'datname="app"' in query for query in queries)
    assert any("findme_db_max_relation_xid_age" in query for query in queries)
    assert not any("vector(0)" in query or "pg_stat_statements" in query for query in queries)


def test_postgres_rules_separate_actionable_degradation_from_missing_telemetry():
    ctl = control()
    rules = yaml.safe_load(ctl.render(ctl.load_config())["rules.yml"])
    groups = [group for group in rules["groups"] if group["name"] == "findme-postgres"]
    assert len(groups) == 1
    alerts = {rule["alert"]: rule for rule in groups[0]["rules"]}
    assert alerts["DatabaseUnavailableToDjango"]["for"] == "3m"
    assert alerts["DatabaseUnavailableToDjango"]["labels"]["notification"] == "actionable"
    assert "pg_up" not in alerts["DatabaseUnavailableToDjango"]["expr"]
    for name in ("DatabaseConnectionExhaustion", "DatabaseLockDegradation", "DatabaseFreezeDanger"):
        assert alerts[name]["labels"]["incident"] == "database"
        assert alerts[name]["labels"]["notification"] == "actionable"
    for name in (
        "DatabaseTelemetryMissing",
        "DatabaseCollectorFailure",
        "DatabaseTransactionFailures",
    ):
        assert "notification" not in alerts[name]["labels"]
    assert not any("disk" in name.lower() for name in alerts)
