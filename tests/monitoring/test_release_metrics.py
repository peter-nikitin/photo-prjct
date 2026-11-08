import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scope", ["vm", "application"])
def test_scope_queries_raw_fresh_samples_with_explicit_evaluation_time(scope):
    checker = load("release_metrics", "deploy/monitoring/prometheus/verify_metrics.py")
    urls = []

    class Transport:
        def request(self, method, path):
            assert method == "GET"
            params = parse_qs(urlsplit(path).query)
            assert params["time"] == ["1000"]
            urls.append(params["query"][0])
            return {
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"values": [[990, "1"]]}]},
            }

    checker.check(Transport(), scope, now=1000)
    if scope == "vm":
        assert len(urls) == 3
        assert any('mountpoint="/"' in item for item in urls)
        assert any('mountpoint="/var/lib/findme-photo"' in item for item in urls)
    else:
        assert len(urls) == 2
        assert any(item.startswith("pg_up") for item in urls)
        assert any(item.startswith("findme_http_requests_total") for item in urls)


@pytest.mark.parametrize("sample", [None, [0, "1"], [990, "NaN"], [1001, "1"], [990, "0"]])
def test_application_rejects_missing_stale_nonfinite_future_or_disconnected_postgres(sample):
    checker = load("release_metrics_failures", "deploy/monitoring/prometheus/verify_metrics.py")

    class Transport:
        def request(self, method, path):
            query = parse_qs(urlsplit(path).query)["query"][0]
            point = sample if query.startswith("pg_up") else [990, "1"]
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": ([{"values": [point]}] if point is not None else []),
                },
            }

    with pytest.raises(checker.control.ControlError):
        checker.check(Transport(), "application", now=1000)


def test_cli_retries_missing_then_accepts_fresh_samples(monkeypatch):
    import sys

    checker = load("release_metrics_retry", "deploy/monitoring/prometheus/verify_metrics.py")
    attempts = []
    monkeypatch.setattr(sys, "argv", ["verify_metrics.py", "vm", "--oidc-config", "oidc.json"])
    monkeypatch.setattr(checker.control, "identity", lambda *_args: "token")
    monkeypatch.setattr(checker.control, "CloudTransport", lambda *_args: object())
    monkeypatch.setattr(checker.time, "monotonic", lambda: 0)
    monkeypatch.setattr(checker.time, "sleep", lambda seconds: attempts.append(seconds))

    def check(_transport, scope):
        assert scope == "vm"
        if not attempts:
            raise checker.control.ControlError("expected sample missing: disk")

    monkeypatch.setattr(checker, "check", check)
    assert checker.main() == 0
    assert attempts == [10]


def test_cli_stops_at_deadline_and_reports_source(monkeypatch, capsys):
    import sys

    checker = load("release_metrics_deadline", "deploy/monitoring/prometheus/verify_metrics.py")
    monkeypatch.setattr(sys, "argv", ["verify_metrics.py", "vm", "--oidc-config", "oidc.json"])
    monkeypatch.setattr(checker.control, "identity", lambda *_args: "token")
    monkeypatch.setattr(checker.control, "CloudTransport", lambda *_args: object())
    ticks = iter([0, 301])
    monkeypatch.setattr(checker.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(checker.time, "sleep", lambda _seconds: pytest.fail("deadline elapsed"))

    def check(_transport, _scope):
        raise checker.control.ControlError("expected sample missing: root disk")

    monkeypatch.setattr(checker, "check", check)
    assert checker.main() == 1
    assert "root disk" in capsys.readouterr().err
