import importlib.util
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "single_dashboard", ROOT / "deploy/monitoring/prometheus/single_vm_dashboard.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dashboard_updates_without_querying_metrics(tmp_path):
    module = load()

    class Transport:
        def __init__(self):
            self.calls = []
            self.current = {
                "id": module.CONFIG["dashboard_id"],
                "folderId": module.CONFIG["folder_id"],
                "etag": "x",
                "title": "old",
                "widgets": [],
            }

        def dashboard_get(self, _):
            self.calls.append("get")
            return self.current

        def dashboard_update(self, request):
            self.calls.append("update")
            self.current.update(
                {key: request[key] for key in ("title", "widgets", "parametrization")}
            )

    transport = Transport()
    module.reconcile(transport, tmp_path / "backup")
    assert transport.calls == ["get", "update", "get"]
    assert transport.current["parametrization"] == module.dashboard()["parametrization"]
    assert (tmp_path / "backup/dashboard.json").exists()


def test_matching_dashboard_is_not_rewritten_or_backed_up(tmp_path):
    module = load()

    class Transport:
        def dashboard_get(self, _):
            return {
                "id": module.CONFIG["dashboard_id"],
                "folderId": module.CONFIG["folder_id"],
                "etag": "a",
                **module.dashboard(),
            }

        def dashboard_update(self, request):
            raise AssertionError("unchanged dashboard must not be rewritten")

    module.reconcile(Transport(), tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_dashboard_top_level_group_rectangles_do_not_overlap():
    widgets = load().dashboard()["widgets"]

    def rectangle(widget):
        position = widget["position"]
        return (
            int(position.get("x", 0)),
            int(position.get("y", 0)),
            int(position["w"]),
            int(position["h"]),
        )

    for first, second in combinations(widgets, 2):
        x1, y1, w1, h1 = rectangle(first)
        x2, y2, w2, h2 = rectangle(second)
        assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1, (
            f"Overlapping groups: {first['group']['title']} and {second['group']['title']}"
        )
