import importlib.util
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
            self.current.update({key: request[key] for key in ("title", "widgets")})

    transport = Transport()
    module.reconcile(transport, tmp_path / "backup")
    assert transport.calls == ["get", "update", "get"]
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
