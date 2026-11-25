import copy
import importlib.util
import json
from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "single_dashboard", ROOT / "deploy/monitoring/prometheus/single_vm_dashboard.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Transport:
    def __init__(self, module):
        self.current = {
            module.CONFIG["dashboard_id"]: {
                "id": module.CONFIG["dashboard_id"],
                "name": "findme-photo-deployment-overview",
                "folderId": module.CONFIG["folder_id"],
                "etag": "1",
                "title": "old",
                "widgets": [],
            },
            "unrelated": {"id": "unrelated", "title": "untouched"},
        }
        self.calls = []

    def dashboard_list(self):
        return list(self.current.values())

    def dashboard_get(self, dashboard_id):
        return copy.deepcopy(self.current[dashboard_id])

    def dashboard_create(self, request):
        self.calls.append(("create", request["name"]))
        dashboard_id = "id-" + request["name"]
        self.current[dashboard_id] = {**request, "id": dashboard_id, "etag": "1"}
        return dashboard_id

    def dashboard_update(self, request):
        dashboard_id = request["dashboardId"]
        self.calls.append(("update", dashboard_id))
        self.current[dashboard_id].update({k: v for k, v in request.items() if k != "dashboardId"})


def test_publish_creates_details_before_overview_and_second_run_is_idle(tmp_path):
    module = load()
    transport = Transport(module)
    module.reconcile(transport, tmp_path / "backup")
    assert len(transport.current) == 7
    assert [c[0] for c in transport.calls] == ["create"] * 5 + ["update"]
    assert transport.current["unrelated"]["title"] == "untouched"
    assert (tmp_path / "backup/overview.json").exists()
    transport.calls.clear()
    module.reconcile(transport, tmp_path / "second")
    assert transport.calls == []
    assert not (tmp_path / "second").exists()


def test_readback_mismatch_fails_before_overview_is_replaced(tmp_path):
    module = load()
    transport = Transport(module)
    create = transport.dashboard_create

    def broken(request):
        dashboard_id = create(request)
        transport.current[dashboard_id]["widgets"] = []
        return dashboard_id

    transport.dashboard_create = broken
    with pytest.raises(module.control.ControlError, match="read-back"):
        module.reconcile(transport, tmp_path / "backup")
    assert transport.current[module.CONFIG["dashboard_id"]]["title"] == "old"
    assert not any(c[0] == "update" for c in transport.calls)


def test_detail_dashboards_preserve_all_charts_and_queue_assignment():
    module = load()
    rendered = module.dashboards()
    assert set(rendered) == {"overview", "upload", "search", "commerce", "web", "infrastructure"}
    original = json.loads(
        (ROOT / "deploy/monitoring/prometheus/single-vm-dashboard.json").read_text()
    )
    original_ids = {c["id"] for c in module.control._dashboard_charts(original)}
    detail_ids = [
        c["id"]
        for key, d in rendered.items()
        if key != "overview"
        for c in module.control._dashboard_charts(d)
    ]
    assert len(detail_ids) == len(set(detail_ids)) == 134
    assert original_ids <= set(detail_ids)
    assert len(module.control._dashboard_charts(rendered["overview"])) <= 8
    expected = {"upload": 7, "search": 4, "commerce": 2}
    for key, count in expected.items():
        queue_groups = rendered[key]["widgets"][:count]
        for group in queue_groups:
            assert len(group["group"]["widgets"]) == 7
            for child in group["group"]["widgets"]:
                position = child["position"]
                assert int(position.get("y", 0)) + int(position["h"]) <= int(group["position"]["h"])
    for key, dashboard in rendered.items():
        assert len(dashboard["links"]) == 5
        for first, second in combinations(dashboard["widgets"], 2):
            a, b = first["position"], second["position"]
            assert int(a.get("y", 0)) + int(a["h"]) <= int(b.get("y", 0)) or int(
                b.get("y", 0)
            ) + int(b["h"]) <= int(a.get("y", 0)), key


def test_partial_publication_can_be_retried_without_duplicate_dashboards(tmp_path):
    module = load()
    transport = Transport(module)
    create = transport.dashboard_create

    def interrupted(request):
        if len(transport.calls) == 1:
            raise module.control.ControlError("interrupted")
        return create(request)

    transport.dashboard_create = interrupted
    with pytest.raises(module.control.ControlError, match="interrupted"):
        module.reconcile(transport, tmp_path / "first")
    transport.dashboard_create = create
    module.reconcile(transport, tmp_path / "retry")
    assert len(transport.current) == 7
    assert len([c for c in transport.calls if c[0] == "create"]) == 5


def test_discovery_error_does_not_create_dashboards(tmp_path):
    module = load()
    transport = Transport(module)

    def denied():
        raise module.control.ControlError("denied")

    transport.dashboard_list = denied
    with pytest.raises(module.control.ControlError, match="denied"):
        module.reconcile(transport, tmp_path / "backup")
    assert transport.calls == []


def test_changed_detail_is_backed_up_and_updated_in_place(tmp_path):
    module = load()
    transport = Transport(module)
    module.reconcile(transport, tmp_path / "first")
    child_id = "id-findme-photo-upload"
    transport.current[child_id]["title"] = "changed"
    transport.calls.clear()
    module.reconcile(transport, tmp_path / "second")
    assert transport.calls == [("update", child_id)]
    assert json.loads((tmp_path / "second/upload.json").read_text())["title"] == "changed"
