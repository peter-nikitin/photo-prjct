import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "deploy/monitoring/prometheus/control.py"


def load_control():
    spec = importlib.util.spec_from_file_location("monitoring_control", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_control_plane_exists():
    assert PATH.is_file(), "declarative control CLI is missing"


@pytest.fixture
def control():
    if not PATH.exists():
        pytest.skip("control plane not implemented")
    return load_control()


def config(control):
    value = control.load_config()
    value.update(
        workspace_id="workspace1",
        channel_name="operator-email",
        cpu_semantics="cumulative_counter",
        type_contract_evidence="reviewed capture",
    )
    return value


def test_offline_render_has_missing_observations_separate(control):
    package = control.render(control.load_config())
    assert "max_over_time(findme_probe_success" in package["rules.yml"]
    assert "[10m]) < 0.5" in package["rules.yml"]
    assert "absent_over_time" in package["rules.yml"]
    assert "increase(findme_http_requests_total" in package["rules.yml"]
    assert len(json.loads(package["dashboard.json"])["widgets"]) == 19


def test_rendered_project_email_receiver_explicitly_sends_recovery(control):
    routing = yaml.safe_load(control.render(config(control))["alertmanager.yml"])
    receiver = next(item for item in routing["receivers"] if item["name"] == "findme-email")
    assert receiver["yandex_monitoring_configs"] == [
        {"channel_names": ["operator-email"], "send_resolved": True}
    ]
    assert routing["route"]["routes"] == [
        {"receiver": "findme-email", "matchers": ['project="findme-photo"']}
    ]
    unmatched = next(item for item in routing["receivers"] if item["name"] == "unmatched")
    assert unmatched["yandex_monitoring_configs"] == [{"channel_names": []}]


def test_activation_rejects_missing_foundation(control):
    cfg = control.load_config()
    cfg["workspace_id"] = ""
    with pytest.raises(control.ControlError, match="workspace_id"):
        control.validate_config(cfg, live=True)


class FakeTransport:
    def __init__(self, control, cfg, *, folder=None, corrupt=False):
        self.control, self.cfg = control, cfg
        self.events = []
        self.dashboard = {
            "id": cfg["dashboard_id"],
            "folderId": folder or cfg["folder_id"],
            "etag": "7",
            "name": "keep",
            "description": "keep",
            "labels": {"preserved": "yes"},
            "widgets": [],
        }
        self.corrupt = corrupt
        self.rules = None

    def dashboard_get(self, dashboard_id):
        self.events.append(("get-dashboard", dashboard_id))
        return self.dashboard

    def wait_rules_evaluation(self, name, *, earliest):
        self.events.append(("evaluated-rules", name))

    def dashboard_update(self, request):
        self.events.append(("update-dashboard", request))
        assert request["etag"] == "7"
        assert request["labels"] == {"preserved": "yes"}
        self.dashboard.update(request)
        if self.corrupt:
            self.dashboard["widgets"] = []

    def request(self, method, path, body=None):
        self.events.append((method, path, body))
        if "/api/v1/query?" in path:
            query = parse_qs(urlsplit(path).query)["query"][0]
            if query.endswith("]"):
                return {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {}, "values": [[1000, "1000"]]}],
                    },
                }
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1000, "1000"]}],
                },
            }
        if method == "GET" and path == "/extensions/v1/rules":
            return {"files": []}
        if method == "DELETE":
            self.rules = None
        if method == "PUT" and path.endswith("/rules"):
            self.rules = body
        if method == "GET" and path.endswith("/rules/findme-photo.yml"):
            return self.rules or {"content": "", "absent": True}
        return {}


def test_apply_preflights_routes_then_owned_rules_and_preserves_dashboard(control, tmp_path):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    control.apply(cfg, transport, tmp_path / "backup", now=1000)
    puts = [event[1] for event in transport.events if event[0] == "PUT"]
    assert puts == ["/extensions/v1/alertmanager", "/extensions/v1/rules"]
    assert not any(event[0] == "DELETE" for event in transport.events)
    assert (tmp_path / "backup/dashboard.json").is_file()
    assert (tmp_path / "backup/rules.json").is_file()


def test_wrong_folder_blocks_all_mutations(control, tmp_path):
    cfg = config(control)
    transport = FakeTransport(control, cfg, folder="wrong")
    with pytest.raises(control.ControlError, match="folder"):
        control.apply(cfg, transport, tmp_path / "backup", now=1000)
    assert not any(event[0] in ("PUT", "update-dashboard") for event in transport.events)


def test_dashboard_readback_failure_is_failure(control, tmp_path):
    cfg = config(control)
    with pytest.raises(control.ControlError, match="read-back"):
        control.apply(cfg, FakeTransport(control, cfg, corrupt=True), tmp_path / "backup", now=1000)


def test_stale_or_nan_samples_block_activation(control):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request

    def stale(method, path, body=None):
        if "/api/v1/query?" in path:
            return {
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"values": [[1, "NaN"]]}]},
            }
        return original(method, path, body)

    transport.request = stale
    with pytest.raises(control.ControlError, match="sample"):
        control.preflight(cfg, transport, now=1000)


def test_counter_cpu_contract_rejected_until_verified(control):
    cfg = config(control)
    cfg["metrics"]["cpu_useful"]["type"] = "gauge"
    with pytest.raises(control.ControlError, match="CPU"):
        control.validate_config(cfg, live=True)


def test_snapshot_error_blocks_success(control):
    with pytest.raises(control.ControlError, match="evaluation"):
        control.verify_snapshots(
            {
                "snapshotByGroup": {
                    "findme-photo": [
                        {"state": "TIMEOUT", "error": "failure", "evaluatedAtTimeEpochMs": 1000000}
                    ]
                }
            },
            now=1000,
        )


def test_snapshot_missing_is_unverified(control):
    with pytest.raises(control.ControlError, match="evaluation"):
        control.verify_snapshots({"snapshotByGroup": {}}, now=1000)


def test_check_reports_routing_unverified(control):
    cfg = config(control)
    result = control.check(cfg, FakeTransport(control, cfg), now=1000)
    assert result["rules_match"] is False
    assert result["dashboard_matches"] is False
    assert result["routing_drift"].startswith("unverified")


def test_cli_missing_workspace_never_attempts_identity(control, capsys, monkeypatch):
    cfg = control.load_config()
    cfg["workspace_id"] = ""
    monkeypatch.setattr(control, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(sys, "argv", ["control.py", "check"])
    assert control.main() == 1
    assert "workspace_id" in capsys.readouterr().err


def test_http_absent_5xx_coalesces_only_when_total_exists(control):
    cfg = config(control)
    expression = control.expressions(cfg)["http_errors"]
    assert "or (0 * sum(increase(findme_http_requests_total[5m])))" in expression


@pytest.mark.parametrize("labels", [{}, {"job": "private-http"}])
def test_selectors_omit_empty_labels_and_preserve_http_5xx_filter(control, labels):
    cfg = config(control)
    cfg["metrics"]["http_requests"]["labels"] = labels
    selectors = control.selectors(cfg)
    expected = (
        'findme_http_requests_total{job="private-http"}' if labels else "findme_http_requests_total"
    )
    assert selectors["http_requests"] == expected
    assert selectors["public_success"] == 'findme_probe_success{check="canonical-health"}'
    expected_5xx = (
        'findme_http_requests_total{job="private-http",status_class="5xx"}'
        if labels
        else 'findme_http_requests_total{status_class="5xx"}'
    )
    assert selectors["http_5xx"] == expected_5xx


def test_owned_rule_404_is_absence_but_other_http_errors_fail_closed(control, monkeypatch):
    from urllib.error import HTTPError

    transport = control.CloudTransport.__new__(control.CloudTransport)
    transport.config = config(control)
    transport.token = "secret-never-print"

    def fail(request, timeout):
        raise HTTPError(request.full_url, 404, "secret-never-print", {}, None)

    monkeypatch.setattr(control, "urlopen", fail)
    assert transport.request("GET", "/extensions/v1/rules/findme-photo.yml")["absent"]
    with pytest.raises(control.ControlError) as error:
        transport.request("PUT", "/extensions/v1/rules", {"content": "anything"})
    assert "secret-never-print" not in str(error.value)


def test_foreign_workspace_rules_block_routing_overwrite(control, tmp_path):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request

    def request(method, path, body=None):
        if method == "GET" and path == "/extensions/v1/rules":
            return {"files": ["another-project.yml"]}
        return original(method, path, body)

    transport.request = request
    with pytest.raises(control.ControlError, match="dedicated"):
        control.apply(cfg, transport, tmp_path / "backup", now=1000)
    assert not any(event[0] == "PUT" for event in transport.events)


def test_restore_first_activation_deletes_only_owned_rules_without_metric_preflight(
    control, tmp_path
):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    backup = tmp_path / "backup"
    control.apply(cfg, transport, backup, now=1000)
    previous = len(transport.events)
    control.restore(cfg, transport, backup, routing_file=None)
    recent = transport.events[previous:]
    assert ("DELETE", "/extensions/v1/rules/findme-photo.yml", None) in recent
    assert not any("/api/v1/query?" in str(event) for event in recent)


def test_restore_rejects_different_workspace(control, tmp_path):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    backup = tmp_path / "backup"
    control.apply(cfg, transport, backup, now=1000)
    cfg["workspace_id"] = "another"
    with pytest.raises(control.ControlError, match="target"):
        control.restore(cfg, transport, backup, routing_file=None)


def test_freshness_uses_response_time_when_scrape_advances_during_preflight(control, monkeypatch):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request
    clock = [1000]
    monkeypatch.setattr(control.time, "time", lambda: clock[0])

    def request(method, path, body=None):
        clock[0] += 1
        response = original(method, path, body)
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query.endswith("]"):
            response["data"]["result"][0]["values"] = [[clock[0] - 1, "1"]]
        return response

    transport.request = request
    control.preflight(cfg, transport)


@pytest.mark.parametrize("observed", ["NaN", "0", "1001"])
def test_fixed_now_rejects_nonfinite_stale_and_future_observations(control, observed):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request

    def request(method, path, body=None):
        response = original(method, path, body)
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query.endswith("]"):
            response["data"]["result"][0]["values"] = [[observed, "1"]]
        return response

    transport.request = request
    with pytest.raises(control.ControlError, match="sample"):
        control.preflight(cfg, transport, now=1000)


def test_public_freshness_bounds_match_two_probe_intervals(control):
    metrics = control.load_config()["metrics"]
    public = {"public_success", "public_duration", "tls_days"}
    assert {metric["max_age"] for key, metric in metrics.items() if key in public} == {600}
    assert {metric["max_age"] for key, metric in metrics.items() if key not in public} == {120}


@pytest.mark.parametrize("days", ["10", "30"])
def test_tls_preflight_uses_fresh_public_observation_older_than_five_minutes(control, days):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request
    selector = control.selectors(cfg)["tls_days"]
    expected = f"min_over_time({selector}[10m])"

    def request(method, path, body=None):
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query == selector + "[600s]":
            return {
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"values": [[640, days]]}]},
            }
        if query == f"min_over_time({selector}[5m])":
            return {"status": "success", "data": {"resultType": "vector", "result": []}}
        return original(method, path, body)

    transport.request = request
    control.preflight(cfg, transport, now=1000)
    assert control.expressions(cfg)["tls"] == expected
    rules = yaml.safe_load(control.render(cfg)["rules.yml"])["groups"][0]["rules"]
    tls_rule = next(rule for rule in rules if rule["alert"] == "TLSCertificateExpiring")
    assert tls_rule["expr"] == expected + " < 14"


def test_fresh_matrix_accepts_public_when_instant_vector_is_empty(control):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request
    selector = control.selectors(cfg)["public_success"]

    def request(method, path, body=None):
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query == selector:
            return {"status": "success", "data": {"resultType": "vector", "result": []}}
        response = original(method, path, body)
        if query == selector + "[600s]":
            response["data"]["result"][0]["values"] = [[700, "1"], [990, "1"]]
        return response

    transport.request = request
    control.preflight(cfg, transport, now=1000)
    queries = [
        parse_qs(urlsplit(event[1]).query)["query"][0]
        for event in transport.events
        if "/api/v1/query?" in event[1]
    ]
    assert selector + "[600s]" in queries
    assert not any(query.startswith("timestamp(") for query in queries)
    assert "findme_http_request_duration_seconds_count[120s]" in queries


@pytest.mark.parametrize(
    "response",
    [
        {"status": "error", "data": {"resultType": "matrix", "result": []}},
        {"status": "success", "data": {"resultType": "vector", "result": []}},
        {"status": "success", "data": {"resultType": "matrix", "result": []}},
        {"status": "success", "data": {"resultType": "matrix", "result": [{"values": []}]}},
        {"status": "success", "data": {"resultType": "matrix", "result": [{"values": [[990]]}]}},
        {"status": "success", "data": {"resultType": "matrix", "result": [{"values": [[0, "1"]]}]}},
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"values": [[1001, "1"]]}]},
        },
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"values": [[990, "NaN"]]}]},
        },
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"values": [["NaN", "1"]]}]},
        },
    ],
)
def test_matrix_freshness_rejects_failed_missing_malformed_or_invalid_points(control, response):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request

    def request(method, path, body=None):
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        return response if query.endswith("]") else original(method, path, body)

    transport.request = request
    with pytest.raises(control.ControlError, match="sample"):
        control.preflight(cfg, transport, now=1000)


def test_matrix_freshness_rejects_stale_latest_point_in_any_observed_series(control):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request

    def request(method, path, body=None):
        response = original(method, path, body)
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query.endswith("]"):
            response["data"]["result"].append(
                {"metric": {"instance": "stale"}, "values": [[0, "1"]]}
            )
        return response

    transport.request = request
    with pytest.raises(control.ControlError, match="sample"):
        control.preflight(cfg, transport, now=1000)


DIAGNOSTIC_IO = ("disk_read_rate", "disk_write_rate", "network_rx_rate", "network_tx_rate")


def test_diagnostic_charts_use_verified_gauges_and_scoped_counter_rates(control):
    cfg = config(control)
    metrics = cfg["metrics"]
    expected = {
        "swap_free": ("sys_memory_SwapFree", "gauge", {"instance": "dev-photo-prjct"}),
        "swap_total": ("sys_memory_SwapTotal", "gauge", {"instance": "dev-photo-prjct"}),
        "inode_free": (
            "sys_filesystem_INodeFree",
            "gauge",
            {"instance": "dev-photo-prjct", "mountpoint": "/"},
        ),
        "inode_total": (
            "sys_filesystem_INodeTotal",
            "gauge",
            {"instance": "dev-photo-prjct", "mountpoint": "/"},
        ),
        "disk_read": (
            "sys_io_Disks_ReadBytes",
            "counter",
            {"instance": "dev-photo-prjct", "disk": "vda"},
        ),
        "disk_write": (
            "sys_io_Disks_WriteBytes",
            "counter",
            {"instance": "dev-photo-prjct", "disk": "vda"},
        ),
        "network_rx": (
            "sys_net_Ifs_RxBytes",
            "counter",
            {"instance": "dev-photo-prjct", "intf": "eth0"},
        ),
        "network_tx": (
            "sys_net_Ifs_TxBytes",
            "counter",
            {"instance": "dev-photo-prjct", "intf": "eth0"},
        ),
    }
    for key, (name, kind, labels) in expected.items():
        assert metrics[key] == {"name": name, "type": kind, "labels": labels, "max_age": 120}
    selectors = control.selectors(cfg)
    expressions = control.expressions(cfg)
    assert expressions["swap_free_gib"] == f"{selectors['swap_free']} / 1073741824"
    assert expressions["swap_total_gib"] == f"{selectors['swap_total']} / 1073741824"
    assert (
        expressions["inode_percent"]
        == f"100 * {selectors['inode_free']} / {selectors['inode_total']}"
    )
    for key, metric in zip(
        DIAGNOSTIC_IO, ("disk_read", "disk_write", "network_rx", "network_tx"), strict=True
    ):
        assert expressions[key] == f"rate({selectors[metric]}[5m])"
    widgets = json.loads(control.render(cfg)["dashboard.json"])["widgets"]
    charts = [widget["multiSourceChart"] for widget in widgets[14:]]
    assert [len(chart["targets"]) for chart in charts] == [2, 1, 2, 2, 1]
    for chart in (charts[0], charts[2], charts[3]):
        assert chart["displayLegend"] is True
    assert [widget["position"] for widget in widgets[14:]] == [
        {"y": "56", "w": "12", "h": "8"},
        {"x": "12", "y": "56", "w": "12", "h": "8"},
        {"y": "64", "w": "12", "h": "8"},
        {"x": "12", "y": "64", "w": "12", "h": "8"},
        {"y": "72", "w": "12", "h": "8"},
    ]
    native = charts[4]["targets"][0]["monitoringTarget"]
    assert (
        native["query"] == '"ua.backlog"{folderId="b1g2qttgfhb4gdunvlge",service="custom",'
        'host="dev-photo-prjct",scope="health"}'
    )
    assert "workspaceId" not in native


def test_zero_swap_is_valid_when_disabled(control):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request
    zero_queries = {
        control.selectors(cfg)[key] + "[120s]" for key in ("swap_free", "swap_total")
    } | {control.expressions(cfg)[key] for key in ("swap_free_gib", "swap_total_gib")}
    seen = set()

    def request(method, path, body=None):
        response = original(method, path, body)
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query in zero_queries:
            seen.add(query)
            point = response["data"]["result"][0]
            point["values" if query.endswith("]") else "value"] = (
                [[1000, "0"]] if query.endswith("]") else [1000, "0"]
            )
        return response

    transport.request = request
    control.preflight(cfg, transport, now=1000)
    assert seen == zero_queries


@pytest.mark.parametrize("key", DIAGNOSTIC_IO)
@pytest.mark.parametrize("value", [None, "NaN"])
def test_missing_or_nonfinite_diagnostic_rate_blocks_apply(control, tmp_path, key, value):
    cfg = config(control)
    transport = FakeTransport(control, cfg)
    original = transport.request
    query_to_fail = control.expressions(cfg)[key]

    def request(method, path, body=None):
        query = parse_qs(urlsplit(path).query).get("query", [""])[0]
        if query == query_to_fail:
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [] if value is None else [{"value": [1000, value]}],
                },
            }
        return original(method, path, body)

    transport.request = request
    with pytest.raises(control.ControlError, match="expression calculation failed: " + key):
        control.apply(cfg, transport, tmp_path / "backup", now=1000)
    assert not any(event[0] in ("PUT", "update-dashboard") for event in transport.events)


def test_render_resolves_histogram_quantile_placeholders_with_numeric_names(control):
    dashboard = json.loads(control.render(config(control))["dashboard.json"])
    targets = dashboard["widgets"][9]["multiSourceChart"]["targets"]
    queries = [target["prometheusTarget"]["query"] for target in targets]
    assert queries == [
        f"histogram_quantile({quantile}, sum by (le) "
        "(rate(findme_http_request_duration_seconds_bucket[5m])))"
        for quantile in ("0.50", "0.95")
    ]
