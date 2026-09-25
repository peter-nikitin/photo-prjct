import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_is_importable_and_covers_only_configured_monitoring_streams() -> None:
    dashboard = json.loads((ROOT / "deploy/monitoring/dashboard.json").read_text(encoding="utf-8"))

    assert dashboard["name"] == "findme-photo-deployment-overview"
    assert dashboard["folderId"] == "__YANDEX_CLOUD_FOLDER_ID__"
    assert dashboard["labels"] == {"managed-by": "repository"}

    charts = {
        widget["chart"]["title"]: widget["chart"]
        for widget in dashboard["widgets"]
        if "chart" in widget
    }
    assert set(charts) == {
        "External health",
        "VM CPU and load",
        "VM memory and swap",
        "Filesystem capacity and inodes",
        "Disk I/O",
        "Network I/O",
        "VM uptime and Unified Agent health",
        "Django request rate",
        "Django 5xx responses",
        "Django request latency (p50 / p95)",
        "Commerce worker health (activation prerequisite)",
    }
    rendered_queries = "\n".join(
        target["query"] for chart in charts.values() for target in chart["queries"]["targets"]
    )
    for metric in (
        "findme_probe_success",
        "findme_probe_duration_seconds",
        "findme_probe_tls_days_remaining",
        "app.findme_http_requests_total",
        "app.findme_http_request_duration_seconds",
    ):
        assert metric in rendered_queries
    assert 'environment="staging"' not in rendered_queries
    assert 'check="canonical-health"' in rendered_queries
    assert 'service="custom"' in rendered_queries
    assert "folderId=" not in rendered_queries
    assert [
        target["query"]
        for target in charts["Django request latency (p50 / p95)"]["queries"]["targets"]
    ] == [
        'histogram_percentile(50, "bin", '
        '"app.findme_http_request_duration_seconds"{service="custom"})',
        'histogram_percentile(95, "bin", '
        '"app.findme_http_request_duration_seconds"{service="custom"})',
    ]
    for metric in (
        "sys.proc.LoadAverage1min",
        "sys.filesystem.FreeB",
        "sys.filesystem.INodeFree",
        "sys.io.Disks.ReadBytes",
        "sys.io.Disks.WriteBytes",
        "sys.net.Ifs.RxBytes",
        "sys.net.Ifs.TxBytes",
        "sys.system.UpTime",
    ):
        assert metric in rendered_queries
    assert "non_negative_derivative(" in rendered_queries
    assert "histogram_percentile(50" in rendered_queries
    assert "histogram_percentile(95" in rendered_queries
    for invalid in (
        "sys.system.Load1",
        "sys.storage.",
        "sys.network.",
        "sys.system.Uptime",
        "rate(",
        "histogram_quantile(",
    ):
        assert invalid not in rendered_queries
    assert "container" not in rendered_queries.lower()
    assert "/var/run/docker.sock" not in rendered_queries


def test_commerce_worker_monitoring_records_only_safe_liveness_and_ready_work_signals() -> None:
    dashboard = json.loads((ROOT / "deploy/monitoring/dashboard.json").read_text(encoding="utf-8"))
    manifest = (ROOT / "deploy/monitoring/alerts.md").read_text(encoding="utf-8")
    rendered = json.dumps(dashboard)

    assert "Commerce worker health" in rendered
    assert "commerce_worker_alive" in rendered
    assert "commerce_oldest_ready_age_seconds" in rendered
    assert "Commerce worker unavailable" in manifest
    assert "Commerce ready work overdue" in manifest
    for forbidden in ("email", "grant", "token", "payment_id", "provider"):
        assert forbidden not in rendered.lower()


def test_alert_manifest_has_the_baseline_and_disabled_commerce_alert_contracts() -> None:
    manifest = (ROOT / "deploy/monitoring/alerts.md").read_text(encoding="utf-8")

    expected_alerts = {
        "Public service unavailable": (
            "findme_probe_success",
            "maximum over the 10-minute window",
            "10 minutes",
        ),
        "TLS certificate expiring": (
            "findme_probe_tls_days_remaining",
            "below 14 days",
            "5 minutes",
        ),
        "VM telemetry missing": ("ua.", "missing agent or host telemetry", "5 minutes"),
        "Disk space critical": ("sys.filesystem.FreeB", "below 10% or 5 GiB", "10 minutes"),
        "Memory pressure": ("sys.memory.MemAvailable", "below 10%", "15 minutes"),
        "CPU pressure": ("sys.system.UsefulTime", "above 90%", "15 minutes"),
        "Application 5xx degradation": (
            "app.findme_http_requests_total",
            "above 20% with at least 5 requests",
            "5 minutes",
        ),
    }
    assert manifest.count("## ") == 9
    for name, required in expected_alerts.items():
        section = manifest.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]
        for value in required:
            assert value in section
        for field in (
            "Selector:",
            "Aggregation:",
            "Evaluation window:",
            "No data:",
            "Notification channel:",
            "Recovery notification:",
        ):
            assert field in section
    public = manifest.split("## Public service unavailable\n", 1)[1].split("\n## ", 1)[0]
    assert "Firing annotation:" in public
    assert "{{#isAlarm}}" in public
    assert "{{#isNoData}}" in public
    assert "No points in evaluation window** to `No data`" in public
    assert "site availability is unconfirmed" in public
    for section in manifest.split("\n## ")[2:8]:
        assert "Firing notification:" in section
    assert "email" in manifest.lower()
    for invalid in ("sys.storage.", "sys.network.", "sys.system.Load1", "sys.system.Uptime"):
        assert invalid not in manifest


def test_alert_selectors_use_folder_as_request_context_not_metric_label() -> None:
    manifest = (ROOT / "deploy/monitoring/alerts.md").read_text(encoding="utf-8")

    assert "__YANDEX_CLOUD_FOLDER_ID__" in manifest.split("## ", 1)[0]
    selectors = [line for line in manifest.splitlines() if line.startswith("- Selector:")]
    assert len(selectors) == 9
    assert all("folderId=" not in selector for selector in selectors)


def test_runbook_preserves_activation_evidence_and_safe_rollback_boundaries() -> None:
    runbook = (ROOT / "docs/runbooks/minimal-monitoring.md").read_text(encoding="utf-8")

    for required in (
        "findme-photo-deployment-overview",
        "findme-photo-deployment-public-service-unavailable",
        "YANDEX_MONITORING_API_KEY",
        "YANDEX_CLOUD_FOLDER_ID",
        "Partially activated.",
        "public endpoint failure",
        "VM/host telemetry loss",
        "application 5xx degradation",
        "resource pressure",
        "agent-only failure",
        "curl --fail --silent --show-error https://findme-photo.ru/health/",
        "yc compute instance get",
        "systemctl is-active unified_agent",
        "/bin/unified_agent --config /etc/yc/unified_agent/config.yml check-config",
        "docker compose",
        "recovery email",
        "controlled failing target",
        "Never remove application or data volumes",
    ):
        assert required in runbook
    activation_evidence = " ".join(
        runbook.split("## Activation evidence", 1)[1].split("###", 1)[0].split()
    )
    assert (
        "The dashboard, alerts, notification channel, and five-minute image-origin VM public probe "
        "have not been activated"
    ) in activation_evidence
    assert "systemctl is-active unified-agent" not in runbook
    assert "/etc/yandex/unified_agent/config.yml" not in runbook


def test_live_monitoring_plan_uses_current_managed_agent_commands() -> None:
    plan = (ROOT / "docs/plans/2026-07-30-minimal-service-monitoring.md").read_text(
        encoding="utf-8"
    )

    live_validation = plan.split("Live validation commands are run only after Task 6 approval", 1)[
        1
    ]
    assert "systemctl is-active unified_agent" in plan
    assert (
        "/bin/unified_agent --config /etc/yc/unified_agent/config.yml check-config"
        in live_validation
    )
    assert "systemctl is-active unified-agent" not in live_validation
    assert "/etc/yandex/unified_agent/config.yml" not in live_validation
