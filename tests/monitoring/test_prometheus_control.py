import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

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
    assert len(json.loads(package["dashboard.json"])["widgets"]) == 14


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
