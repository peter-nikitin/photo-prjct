import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_application_check_succeeds_without_completion_traffic():
    checker = load("release_metrics", "deploy/monitoring/prometheus/verify_metrics.py")
    samples = {
        ("findme_http_requests_total", "findme-http", None): 1,
        ("pg_up", "findme-postgres", None): 1,
        ("findme_gallery_search_queue_depth", "findme-http", None): 0,
        ("findme_selfie_search_queue_depth", "findme-http", "recognition"): 0,
        ("findme_selfie_search_queue_depth", "findme-http", "matching"): 0,
    }

    class Transport:
        def read_samples(self, selectors, *, now):
            return [
                [[(990, samples[key])]] if key in samples else []
                for selector in selectors
                for key in [(selector["__name__"], selector["job"], selector.get("stage"))]
            ]

    checker.check(Transport(), "application", now=1000)


@pytest.mark.parametrize("listening", [1, 0, None])
def test_queue_telemetry_check_requires_a_live_listener_without_completion_traffic(listening):
    checker = load("release_queue_telemetry", "deploy/monitoring/prometheus/verify_metrics.py")

    class Transport:
        def read_samples(self, selectors, *, now):
            return [
                [[(990, listening)]]
                if listening is not None
                and selector
                == {
                    "__name__": "findme_queue_telemetry_listening",
                    "job": "findme-queue-telemetry",
                }
                else []
                for selector in selectors
            ]

    if listening == 1:
        checker.check(Transport(), "queue-telemetry", now=1000)
    else:
        with pytest.raises(checker.control.ControlError):
            checker.check(Transport(), "queue-telemetry", now=1000)


@pytest.mark.parametrize("sample", [None, (0, 1), (990, float("nan")), (1001, 1), (990, 0)])
def test_application_rejects_missing_stale_nonfinite_future_or_disconnected_postgres(sample):
    checker = load("release_metrics_failures", "deploy/monitoring/prometheus/verify_metrics.py")

    class Transport:
        def read_samples(self, selectors, *, now):
            return [
                [[sample]]
                if selector["__name__"] == "pg_up" and sample is not None
                else []
                if selector["__name__"] == "pg_up"
                else [[(990, 0)]]
                for selector in selectors
            ]

    with pytest.raises(checker.control.ControlError):
        checker.check(Transport(), "application", now=1000)


@pytest.mark.parametrize("has_current_http_series", [True, False])
def test_application_ignores_empty_http_series_but_requires_samples(has_current_http_series):
    checker = load("release_metrics_empty_series", "deploy/monitoring/prometheus/verify_metrics.py")

    class Transport:
        def read_samples(self, selectors, *, now):
            http_series = [[], [(990, 1)]] if has_current_http_series else [[], []]
            return [
                http_series
                if selector["__name__"] == "findme_http_requests_total"
                else [[(990, 1)]]
                for selector in selectors
            ]

    if has_current_http_series:
        checker.check(Transport(), "application", now=1000)
    else:
        with pytest.raises(checker.control.ControlError, match="findme_http_requests_total"):
            checker.check(Transport(), "application", now=1000)


def test_freshness_is_measured_after_slow_read(monkeypatch):
    checker = load("release_metrics_clock", "deploy/monitoring/prometheus/verify_metrics.py")
    monkeypatch.setattr(checker.time, "time", lambda: 1200)

    class Transport:
        def read_samples(self, selectors, *, now):
            assert now is None
            return [[[(1000, 1)]] for _ in selectors]

    with pytest.raises(checker.control.ControlError, match="stale"):
        checker.check(Transport(), "vm")


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
