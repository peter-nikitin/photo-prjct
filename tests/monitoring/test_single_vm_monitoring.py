import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dashboard_reconcile_only_updates_dashboard_and_requires_fresh_samples(tmp_path):
    import time

    import pytest

    module = load("single_dashboard", "deploy/monitoring/prometheus/single_vm_dashboard.py")
    now = time.time()

    class Transport:
        def __init__(self, timestamp):
            self.timestamp = timestamp
            self.calls = []
            self.current = {
                "id": module.CONFIG["dashboard_id"],
                "folderId": module.CONFIG["folder_id"],
                "etag": "x",
                "title": "old",
                "widgets": [],
            }

        def request(self, method, url, *args):
            self.calls.append((method, url))
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[self.timestamp, "1"]]}],
                },
            }

        def dashboard_get(self, _):
            self.calls.append(("dashboard", "get"))
            return self.current

        def dashboard_update(self, request):
            self.calls.append(("dashboard", "update"))
            self.current.update({k: request[k] for k in ("title", "widgets")})

    stale = Transport(now - 1000)
    with pytest.raises(module.control.ControlError):
        module.reconcile(stale, tmp_path / "stale", now=now)
    assert ("dashboard", "update") not in stale.calls
    fresh = Transport(now)
    module.reconcile(fresh, tmp_path / "backup", now=now)
    assert ("dashboard", "update") in fresh.calls
    assert all(method == "GET" for method, url in fresh.calls if method != "dashboard")
    assert all("/extensions/" not in url for _, url in fresh.calls)


def test_matching_dashboard_is_not_rewritten_after_freshness_check(tmp_path):
    import time

    module = load("matching_dashboard", "deploy/monitoring/prometheus/single_vm_dashboard.py")

    class Transport:
        def __init__(self):
            self.requests = []

        def request(self, method, url, *args):
            self.requests.append((method, url))
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[time.time(), "1"]]}],
                },
            }

        def dashboard_get(self, _):
            return {
                "id": module.CONFIG["dashboard_id"],
                "folderId": module.CONFIG["folder_id"],
                "etag": "a",
                **module.dashboard(),
            }

        def dashboard_update(self, request):
            raise AssertionError("unchanged dashboard must not be rewritten")

    transport = Transport()
    module.reconcile(transport, tmp_path / "backup")
    assert len(transport.requests) == len(module.SOURCES)
    assert not (tmp_path / "backup").exists()


def test_matching_dashboard_still_requires_fresh_sources(tmp_path):
    import time

    import pytest

    module = load("stale_matching_dashboard", "deploy/monitoring/prometheus/single_vm_dashboard.py")

    class Transport:
        def request(self, method, url, *args):
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[time.time() - 1000, "1"]]}],
                },
            }

        def dashboard_get(self, _):
            pytest.fail("stale sources must fail before dashboard access")

    with pytest.raises(module.control.ControlError, match="stale"):
        module.reconcile(Transport(), tmp_path / "backup")
