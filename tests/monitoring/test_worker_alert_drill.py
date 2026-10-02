"""Finite synthetic evaluator rehearsal; production worker series remain untouched."""

import base64
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "deploy/monitoring/prometheus"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SOURCE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules():
    return load("control"), load("drill")


def config(control):
    value = control.load_config()
    value["workspace_id"] = "reviewed-workspace"
    value["worker_alerts_enabled"] = True
    return value


def test_offline_cli_renders_enabled_drill_from_explicit_disabled_profile_but_live_prepare_rejects(
    tmp_path,
):
    control = load("control")
    git_config = control.load_config()
    assert git_config["worker_alerts_enabled"] is True
    disabled_config = tmp_path / "disabled-environment.json"
    disabled_config.write_text(json.dumps({**git_config, "worker_alerts_enabled": False}))
    rendered = tmp_path / "rendered"
    offline = subprocess.run(
        [
            sys.executable,
            str(SOURCE / "drill.py"),
            "render",
            "--config",
            str(disabled_config),
            "--output",
            str(rendered),
        ],
        capture_output=True,
        text=True,
    )
    assert offline.returncode == 0, offline.stderr
    rules = yaml.safe_load((rendered / "findme-worker-activation-drill.yml").read_text())
    assert rules["groups"][0]["name"] == "findme-worker-activation-drill"
    assert (rendered / "drill-rule-tests.yml").is_file()

    receipt = tmp_path / "receipt.json"
    live = subprocess.run(
        [
            sys.executable,
            str(SOURCE / "drill.py"),
            "prepare",
            "--config",
            str(disabled_config),
            "--receipt",
            str(receipt),
            "--run-id",
            "123456",
            "--revision",
            "a" * 40,
        ],
        capture_output=True,
        text=True,
    )
    assert live.returncode != 0
    assert "worker alert profile must be Git-enabled and live-applied" in live.stderr
    assert not receipt.exists()


def test_drill_clones_exact_worker_rules_with_synthetic_finite_inputs(modules):
    control, drill = modules
    cfg = config(control)
    start = 1_000_000_000
    rendered = control.render(cfg)["rules.yml"]
    worker = next(
        group for group in yaml.safe_load(rendered)["groups"] if group["name"] == "findme-workers"
    )
    cloned = yaml.safe_load(drill.render_rules(control, cfg, "123456", start))["groups"][0]
    assert cloned["interval"] == worker["interval"]
    assert [rule["alert"] for rule in cloned["rules"]] == [
        rule["alert"] for rule in worker["rules"]
    ]
    for source, target in zip(worker["rules"], cloned["rules"], strict=True):
        assert target.get("for") == source.get("for")
        assert target["labels"]["project"] == source["labels"]["project"]
        assert target["labels"]["severity"] == source["labels"]["severity"]
        assert target["labels"]["drill"] == "worker-activation"
        assert target["labels"]["drill_run"] == "123456"
        assert target["annotations"]["summary"].startswith("SYNTHETIC DRILL")
        assert str(start + drill.EXPIRY_SECONDS) in target["expr"]
        assert not re.search(r"\bworker_[A-Za-z0-9_]+\b", target["expr"])
    saturated = next(rule for rule in cloned["rules"] if rule["alert"] == "WorkerPoolSaturated")
    assert "[90s:30s]" in saturated["expr"]
    assert '"bulk"' in saturated["expr"] and '"selfie"' in saturated["expr"]
    assert "findme-photo" in rendered


def test_drill_rejects_unknown_worker_input_and_disabled_profile(modules, monkeypatch):
    control, drill = modules
    cfg = config(control)
    cfg["worker_alerts_enabled"] = False
    with pytest.raises(drill.DrillError):
        drill.render_rules(control, cfg, "123456", 1_000_000_000)
    cfg["worker_alerts_enabled"] = True
    original = control.render

    def changed(value):
        package = original(value)
        package["rules.yml"] = package["rules.yml"].replace(
            "worker_pool_running_instances", "worker_unknown_live_metric"
        )
        return package

    monkeypatch.setattr(control, "render", changed)
    with pytest.raises(drill.DrillError, match="unknown worker metric"):
        drill.render_rules(control, cfg, "123456", 1_000_000_000)

    def changed_other(value):
        package = original(value)
        package["rules.yml"] = package["rules.yml"].replace("worker_pool_running_instances", "up")
        return package

    monkeypatch.setattr(control, "render", changed_other)
    with pytest.raises(drill.DrillError, match="reviewed worker predicate changed"):
        drill.render_rules(control, cfg, "123456", 1_000_000_000)


def test_promtool_fixture_pins_both_pool_saturation_source_and_node_cases(modules, tmp_path):
    control, drill = modules
    rules_path = tmp_path / drill.DRILL_RULES
    rules_path.write_text(drill.render_rules(control, config(control), "123456", 0))
    fixture = drill.write_promtool_fixture(rules_path)
    tests = yaml.safe_load(fixture.read_text())["tests"]
    checks = tests[0]["alert_rule_test"]
    assert any(
        item["alertname"] == "WorkerPoolSaturated"
        and item["eval_time"] == "11m"
        and {alert["exp_labels"]["pool"] for alert in item["exp_alerts"]} == {"bulk", "selfie"}
        for item in checks
    )
    assert any(
        item["alertname"] == "WorkerHostDiagnosticsMissing"
        and item["eval_time"] == "15m"
        and {alert["exp_labels"]["pool"] for alert in item["exp_alerts"]} == {"selfie"}
        for item in checks
    )
    assert any(
        item["alertname"] == "WorkerHostDiagnosticsMissing"
        and item["eval_time"] == "19m"
        and {alert["exp_labels"]["pool"] for alert in item["exp_alerts"]} == {"bulk"}
        for item in checks
    )


class Transport:
    def __init__(self, control, config):
        self.control = control
        self.production = base64.b64encode(control.render(config)["rules.yml"].encode()).decode()
        self.drill = None
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/extensions/v1/rules":
            if method == "GET":
                return {
                    "files": ["findme-photo.yml"]
                    + (["findme-worker-activation-drill.yml"] if self.drill else [])
                }
            assert method == "PUT"
            self.drill = body["content"]
            return {}
        if path == "/extensions/v1/rules/findme-photo.yml":
            return {"content": self.production}
        if path == "/extensions/v1/rules/findme-worker-activation-drill.yml":
            if method == "GET":
                return {"content": self.drill} if self.drill else {"absent": True}
            assert method == "DELETE"
            self.drill = None
            return {}
        if path == "/extensions/v1/rules/findme-worker-activation-drill.yml/snapshots":
            return self.snapshot
        if path.startswith("/api/v1/query?"):
            return self.alerts
        raise AssertionError((method, path))


def test_drill_run_receipt_precedes_put_and_cleanup_is_exact(modules, tmp_path, monkeypatch):
    control, drill = modules
    cfg = config(control)
    transport = Transport(control, cfg)
    monkeypatch.setattr(control, "_preflight_workers", lambda *_args, **_kwargs: None)
    receipt_path = tmp_path / "receipt.json"
    original_request = transport.request

    def checked_request(method, path, body=None):
        if method == "PUT":
            assert receipt_path.is_file()
        return original_request(method, path, body)

    transport.request = checked_request
    receipt = drill.start_run(
        control, cfg, transport, receipt_path, "123456", "a" * 40, now=1_000_000_000
    )
    assert receipt["workspace_id"] == cfg["workspace_id"]
    assert transport.drill
    with pytest.raises(drill.DrillError):
        drill.start_run(
            control, cfg, transport, tmp_path / "second.json", "789", "a" * 40, now=1_000_000_001
        )
    transport.drill = base64.b64encode(b"not owned").decode()
    with pytest.raises(drill.DrillError, match="content"):
        drill.cleanup(control, cfg, transport, receipt_path, "123456", "a" * 40)
    assert transport.drill
    transport.drill = base64.b64encode(
        drill.render_rules(control, cfg, "123456", 1_000_000_000).encode()
    ).decode()
    drill.cleanup(control, cfg, transport, receipt_path, "123456", "a" * 40)
    assert transport.drill is None
    assert (
        transport.production == base64.b64encode(control.render(cfg)["rules.yml"].encode()).decode()
    )


def test_preuploaded_receipt_survives_uncertain_put_for_exact_cleanup(
    modules, tmp_path, monkeypatch
):
    control, drill = modules
    cfg = config(control)
    transport = Transport(control, cfg)
    monkeypatch.setattr(control, "_preflight_workers", lambda *_args, **_kwargs: None)
    path = tmp_path / "receipt.json"
    drill.prepare_receipt(control, cfg, path, "123456", "a" * 40, now=1_000_000_000)
    original = transport.request

    def uncertain(method, endpoint, body=None):
        result = original(method, endpoint, body)
        if method == "PUT":
            raise control.ControlError("uncertain Monitoring PUT")
        return result

    transport.request = uncertain
    with pytest.raises(control.ControlError):
        drill.start_prepared(control, cfg, transport, path, "123456", "a" * 40, now=1_000_000_001)
    assert path.exists() and transport.drill
    transport.request = original
    drill.cleanup(control, cfg, transport, path, "123456", "a" * 40)
    assert transport.drill is None


def test_receipt_rejects_wrong_run_workspace_revision_and_modified_production(
    modules, tmp_path, monkeypatch
):
    control, drill = modules
    cfg = config(control)
    transport = Transport(control, cfg)
    monkeypatch.setattr(control, "_preflight_workers", lambda *_args, **_kwargs: None)
    path = tmp_path / "receipt.json"
    drill.start_run(control, cfg, transport, path, "123456", "a" * 40, now=1_000_000_000)
    for run, revision in (("wrong", "a" * 40), ("123456", "b" * 40)):
        with pytest.raises(drill.DrillError):
            drill.cleanup(control, cfg, transport, path, run, revision)
    cfg["workspace_id"] = "other-workspace"
    with pytest.raises(drill.DrillError):
        drill.cleanup(control, cfg, transport, path, "123456", "a" * 40)
    cfg["workspace_id"] = "reviewed-workspace"
    transport.production = base64.b64encode(b"changed production").decode()
    with pytest.raises(drill.DrillError):
        drill.cleanup(control, cfg, transport, path, "123456", "a" * 40)
    assert transport.drill


def test_status_requires_fresh_exact_evaluation_and_never_claims_delivery(
    modules, tmp_path, monkeypatch
):
    control, drill = modules
    cfg = config(control)
    transport = Transport(control, cfg)
    monkeypatch.setattr(control, "_preflight_workers", lambda *_args, **_kwargs: None)
    path = tmp_path / "receipt.json"
    start = 1_000_000_000
    drill.start_run(control, cfg, transport, path, "123456", "a" * 40, now=start)
    evaluation = start + 10 * 60
    transport.snapshot = {
        "snapshotByGroup": {
            "findme-worker-activation-drill": [
                {"record": alert, "state": "OK", "evaluatedAtTimeEpochMs": evaluation * 1000}
                for alert in drill.EXPECTED_ALERTS
            ]
        }
    }
    transport.alerts = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        "__name__": "ALERTS",
                        "alertname": "WorkerPoolSaturated",
                        "pool": pool,
                        "drill": "worker-activation",
                        "drill_run": "123456",
                        "alertstate": "firing",
                    },
                    "value": [evaluation, "1"],
                }
                for pool in ("bulk", "selfie")
            ],
        },
    }
    observed = drill.status(control, cfg, transport, path, "123456", "a" * 40, now=evaluation + 150)
    assert observed["phase"] == "saturated"
    assert observed["delivery"] == "unverified"
    assert {item["pool"] for item in observed["alerts"]} == {"bulk", "selfie"}
    transport.snapshot["snapshotByGroup"]["findme-worker-activation-drill"][0][
        "evaluatedAtTimeEpochMs"
    ] = (evaluation - 600) * 1000
    with pytest.raises(drill.DrillError):
        drill.status(control, cfg, transport, path, "123456", "a" * 40, now=evaluation + 150)


def test_status_audits_complete_captured_trace_with_effective_predicate_clock(
    modules, tmp_path, monkeypatch
):
    # Complete elapsed timestamps/active states from cleaned-up live drill 36956138231.
    start = 1790908473
    control, drill, cfg, transport, path, _, _ = alert_status_fixture(
        modules, tmp_path, monkeypatch, start=start
    )
    pools = ("bulk", "selfie")
    ready = [("WorkerReadyWorkOverdue", pool, "firing") for pool in pools]
    source = (
        "WorkerCloudObservationMissing",
        "WorkerNativePublisherMissing",
        "WorkerQueueObservationMissing",
    )
    diagnostics = ("WorkerHostDiagnosticsMissing", "WorkerRuntimeDiagnosticsMissing")
    patterns = (
        [],
        ready,
        [("WorkerPoolSaturated", pool, "pending") for pool in pools] + ready,
        [("WorkerPoolSaturated", pool, "firing") for pool in pools] + ready,
        [(alert, "bulk", "firing") for alert in source]
        + [(alert, "selfie", "firing") for alert in diagnostics],
        [(alert, pool, "firing") for alert in diagnostics for pool in pools],
        [(alert, "selfie", "firing") for alert in source]
        + [(alert, "bulk", "firing") for alert in diagnostics],
    )
    trace = (
        (7, 0),
        (94, 0),
        (124, 0),
        (184, 0),
        (245, 0),
        (302, 0),
        (381, 1),
        (425, 2),
        (484, 2),
        (541, 2),
        (604, 2),
        (694, 3),
        (721, 3),
        (783, 3),
        (842, 3),
        (903, 4),
        (961, 4),
        (1021, 4),
        (1082, 4),
        (1144, 5),
        (1212, 5),
        (1263, 6),
        (1325, 6),
        (1384, 0),
        (1442, 0),
        (1503, 0),
        (1564, 0),
        (1622, 0),
        (1681, 0),
    )
    labels = transport.alerts["data"]["result"][0]["metric"]
    seen, first, observations = set(), {}, []
    for elapsed, pattern in trace:
        evaluated_at = start + elapsed
        for entry in transport.snapshot["snapshotByGroup"]["findme-worker-activation-drill"]:
            entry["evaluatedAtTimeEpochMs"] = evaluated_at * 1000
        transport.alerts["data"]["result"] = [
            {
                "metric": {**labels, "alertname": alert, "pool": pool, "alertstate": state},
                "value": [evaluated_at, "1"],
            }
            for alert, pool, state in patterns[pattern]
        ]
        observed = drill.status(
            control, cfg, transport, path, "123456", "a" * 40, now=evaluated_at + 150
        )
        assert transport.calls[-2][1].endswith(f"&time={evaluated_at}")
        observations.append(observed)
        before = seen.copy()
        drill.audit_observation(start, observed, seen)
        for evidence in seen - before:
            first[evidence] = elapsed
    assert seen == drill.REQUIRED_EVIDENCE
    assert first == {
        "healthy": 245,
        "saturation-pending": 425,
        "saturation-firing": 721,
        "source-missing": 1021,
        "retained-stale": 1263,
        "recovered": 1503,
    }
    for observed in observations:
        assert observed["predicate_at"] == observed["evaluated_at"] - 120
        assert observed["phase"] == drill._scenario(start, observed["predicate_at"])
        assert all(item["sample_at"] == observed["evaluated_at"] for item in observed["alerts"])
        assert observed["delivery"] == "unverified"
    assert observations[0]["predicate_at"] < start


def test_finite_audit_requires_both_pool_pending_firing_missing_stale_and_recovery(modules):
    _, drill = modules
    seen = set()

    def observed(minute, state_rows):
        return {
            "evaluated_at": 1_000_000_000 + minute * 60 + 120,
            "predicate_at": 1_000_000_000 + minute * 60,
            "alerts": [
                {"alert": alert, "pool": pool, "state": state, "sample_at": 0}
                for alert, pool, state in state_rows
            ],
        }

    drill.audit_observation(1_000_000_000, observed(2, []), seen)
    drill.audit_observation(
        1_000_000_000,
        observed(6, [("WorkerPoolSaturated", pool, "pending") for pool in ("bulk", "selfie")]),
        seen,
    )
    drill.audit_observation(
        1_000_000_000,
        observed(
            11,
            [
                (alert, pool, "firing")
                for alert in ("WorkerReadyWorkOverdue", "WorkerPoolSaturated")
                for pool in ("bulk", "selfie")
            ],
        ),
        seen,
    )
    for minute, source_pool, node_pool in ((15, "bulk", "selfie"), (19, "selfie", "bulk")):
        drill.audit_observation(
            1_000_000_000,
            observed(
                minute,
                [(alert, source_pool, "firing") for alert in drill.EXPECTED_ALERTS[2:5]]
                + [(alert, node_pool, "firing") for alert in drill.EXPECTED_ALERTS[5:]],
            ),
            seen,
        )
    drill.audit_observation(1_000_000_000, observed(24, []), seen)
    assert seen == drill.REQUIRED_EVIDENCE


@pytest.mark.parametrize(
    "elapsed,problem",
    [(1139, "stale"), (1260, "stale"), (1143, "pool"), (1143, "pending"), (1379, "recovery")],
)
def test_effective_clock_does_not_credit_early_or_wrong_state_evidence(modules, elapsed, problem):
    _, drill = modules
    start = 1_000_000_000
    states = [
        {"alert": alert, "pool": "selfie", "state": "firing"}
        for alert in drill.EXPECTED_ALERTS[2:5]
    ] + [{"alert": alert, "pool": "bulk", "state": "firing"} for alert in drill.EXPECTED_ALERTS[5:]]
    if problem == "pool":
        for item in states:
            item["pool"] = "bulk" if item["pool"] == "selfie" else "selfie"
    elif problem == "pending":
        states[0]["state"] = "pending"
    elif problem == "recovery":
        states = []
    seen = set()
    drill.audit_observation(
        start,
        {"evaluated_at": start + elapsed + 120, "predicate_at": start + elapsed, "alerts": states},
        seen,
    )
    assert seen == set()


def alert_status_fixture(modules, tmp_path, monkeypatch, *, minute=11, start=1_000_000_000):
    control, drill = modules
    cfg = config(control)
    transport = Transport(control, cfg)
    monkeypatch.setattr(control, "_preflight_workers", lambda *_args, **_kwargs: None)
    path = tmp_path / "receipt.json"
    drill.start_run(control, cfg, transport, path, "123456", "a" * 40, now=start)
    evaluation = start + minute * 60 + 120
    transport.snapshot = {
        "snapshotByGroup": {
            "findme-worker-activation-drill": [
                {"record": alert, "state": "OK", "evaluatedAtTimeEpochMs": evaluation * 1000}
                for alert in drill.EXPECTED_ALERTS
            ]
        }
    }
    transport.alerts = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        "__name__": "ALERTS",
                        "alertname": "WorkerPoolSaturated",
                        "pool": "bulk",
                        "drill": "worker-activation",
                        "drill_run": "123456",
                        "alertstate": "pending",
                    },
                    "value": [evaluation, "0.0"],
                }
            ],
        },
    }
    return control, drill, cfg, transport, path, start, evaluation


def test_status_reads_inactive_pending_and_active_firing_without_false_phase_evidence(
    modules, tmp_path, monkeypatch
):
    control, drill, cfg, transport, path, start, evaluation = alert_status_fixture(
        modules, tmp_path, monkeypatch
    )
    labels = transport.alerts["data"]["result"][0]["metric"]
    transport.alerts["data"]["result"] = [
        {
            "metric": {**labels, "alertname": alert, "pool": pool, "alertstate": state},
            "value": [evaluation, value],
        }
        for pool in ("bulk", "selfie")
        for alert, state, value in (
            ("WorkerPoolSaturated", "pending", "0.0"),
            ("WorkerPoolSaturated", "firing", "1.0"),
            ("WorkerReadyWorkOverdue", "firing", "1.0"),
        )
    ]
    observed = drill.status(control, cfg, transport, path, "123456", "a" * 40, now=evaluation + 150)
    assert len(observed["alerts"]) == 4
    assert all(item["state"] == "firing" for item in observed["alerts"])
    assert observed["delivery"] == "unverified"
    seen = set()
    drill.audit_observation(start, observed, seen)
    assert seen == {"saturation-firing"}


def test_status_all_zero_states_are_inactive_recovery_not_delivery(modules, tmp_path, monkeypatch):
    control, drill, cfg, transport, path, start, evaluation = alert_status_fixture(
        modules, tmp_path, monkeypatch, minute=24
    )
    labels = transport.alerts["data"]["result"][0]["metric"]
    transport.alerts["data"]["result"] = [
        {
            "metric": {**labels, "pool": pool, "alertstate": state},
            "value": [evaluation, "0"],
        }
        for pool in ("bulk", "selfie")
        for state in ("pending", "firing")
    ]
    observed = drill.status(control, cfg, transport, path, "123456", "a" * 40, now=evaluation + 150)
    assert observed["alerts"] == []
    assert observed["phase"] == "recovered"
    assert observed["delivery"] == "unverified"
    seen = set()
    drill.audit_observation(start, observed, seen)
    assert seen == {"recovered"}


@pytest.mark.parametrize("value", ["-1", "0.5", "2", "NaN", "Inf"])
def test_status_rejects_nonbinary_alert_state_values(modules, tmp_path, monkeypatch, value):
    control, drill, cfg, transport, path, _, evaluation = alert_status_fixture(
        modules, tmp_path, monkeypatch
    )
    transport.alerts["data"]["result"][0]["value"][1] = value
    with pytest.raises(drill.DrillError, match="identity or timestamp invalid"):
        drill.status(control, cfg, transport, path, "123456", "a" * 40, now=evaluation + 150)


@pytest.mark.parametrize(
    "problem",
    ["duplicate", "drill", "run", "alert", "pool", "state", "missing", "stale", "future", "nan"],
)
def test_status_validates_inactive_zero_rows_before_excluding_them(
    modules, tmp_path, monkeypatch, problem
):
    control, drill, cfg, transport, path, _, evaluation = alert_status_fixture(
        modules, tmp_path, monkeypatch
    )
    rows = transport.alerts["data"]["result"]
    row = rows[0]
    if problem == "duplicate":
        rows.append({**row, "value": [evaluation, "1"]})
    elif problem == "missing":
        del row["metric"]["alertname"]
    elif problem in {"stale", "future", "nan"}:
        row["value"][0] = {
            "stale": evaluation - 91,
            "future": evaluation + 91,
            "nan": "NaN",
        }[problem]
    else:
        key = {
            "drill": "drill",
            "run": "drill_run",
            "alert": "alertname",
            "pool": "pool",
            "state": "alertstate",
        }[problem]
        row["metric"][key] = "unexpected"
    with pytest.raises(drill.DrillError):
        drill.status(control, cfg, transport, path, "123456", "a" * 40, now=evaluation + 150)


def test_run_failure_keeps_unverified_report_and_exactly_cleans_owned_file(
    modules, tmp_path, monkeypatch
):
    control, drill = modules
    cfg = config(control)
    transport = Transport(control, cfg)
    monkeypatch.setattr(control, "_preflight_workers", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(drill.time, "time", lambda: 1_000_000_000)

    def failed_status(*_args, **_kwargs):
        raise drill.DrillError("drill ALERTS query failed")

    monkeypatch.setattr(drill, "status", failed_status)
    receipt = tmp_path / "receipt.json"
    report = tmp_path / "report.json"
    drill.prepare_receipt(control, cfg, receipt, "123456", "a" * 40, now=1_000_000_000)
    with pytest.raises(drill.DrillError, match="ALERTS"):
        drill.run(control, cfg, transport, receipt, report, "123456", "a" * 40)
    assert receipt.exists()
    assert transport.drill is None
    result = json.loads(report.read_text())
    assert result["cleanup"] == "confirmed"
    assert result["delivery"] == "unverified until recipient receipt"


def test_workflow_reuses_protected_serial_monitoring_path_for_drill():
    workflow = (ROOT / ".github/workflows/monitoring.yml").read_text()
    assert "findme-monitoring-production" in workflow
    assert "environment: monitoring" in workflow
    assert "drill-run" in workflow
    assert "drill-status" in workflow
    assert "drill-cleanup" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions: read" in workflow


def test_prior_drill_recovery_binds_source_run_even_after_main_advances(modules):
    _, drill = modules
    old_sha = "a" * 40
    current_main_sha = "b" * 40
    assert old_sha != current_main_sha
    source = {
        "id": 123456,
        "path": ".github/workflows/monitoring.yml",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": old_sha,
        "repository": {"full_name": "example/photo-prjct"},
    }
    assert drill.source_run_revision(source, "example/photo-prjct", "123456") == old_sha
    for changed in (
        {**source, "id": 999},
        {**source, "event": "pull_request"},
        {**source, "head_branch": "feature"},
        {**source, "path": ".github/workflows/other.yml"},
        {**source, "repository": {"full_name": "other/repo"}},
        {**source, "head_sha": "not-a-sha"},
    ):
        with pytest.raises(drill.DrillError):
            drill.source_run_revision(changed, "example/photo-prjct", "123456")

    workflow = (ROOT / ".github/workflows/monitoring.yml").read_text()
    assert 'test "$MONITORING_REVISION" = "$GITHUB_SHA"' in workflow
    assert "DRILL_SOURCE_SHA" in workflow
    assert "ref: ${{ steps.source.outputs.sha }}" in workflow
    assert '--revision "$DRILL_SOURCE_SHA"' in workflow
    steps = yaml.safe_load(workflow)["jobs"]["reconcile"]["steps"]
    bound = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Bind prior drill to a trusted source Monitoring run"
    )
    checkout = next(
        i
        for i, step in enumerate(steps)
        if step.get("with", {}).get("ref") == "${{ steps.source.outputs.sha }}"
    )
    configured = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Prepare isolated tools and foundation inputs"
    )
    assert bound < checkout < configured
