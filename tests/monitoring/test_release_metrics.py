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


@pytest.mark.parametrize("scope", ["vm", "application"])
def test_scope_reads_exact_selectors_in_one_batch(scope):
    checker = load("release_metrics", "deploy/monitoring/prometheus/verify_metrics.py")
    calls = []

    class Transport:
        def read_samples(self, selectors, *, now):
            calls.append(selectors)
            return [[[(990, 1.0)]] for _ in selectors]

    checker.check(Transport(), scope, now=1000)
    assert len(calls) == 1
    selectors = calls[0]
    if scope == "vm":
        assert len(selectors) == 3
        assert {item.get("mountpoint") for item in selectors} >= {"/", "/var/lib/findme-photo"}
    else:
        assert {item["__name__"] for item in selectors} >= {
            "pg_up",
            "findme_http_requests_total",
            "findme_gallery_search_queue_depth",
            "findme_gallery_search_completed_total",
            "findme_gallery_search_phase_duration_seconds_total",
        }
        assert {item.get("phase") for item in selectors if "phase" in item} == {
            "queue",
            "processing",
        }


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


def test_application_accepts_zero_initialized_search_metrics_and_rejects_each_missing_phase():
    checker = load("release_metrics_search", "deploy/monitoring/prometheus/verify_metrics.py")

    class Transport:
        missing = None

        def read_samples(self, selectors, *, now):
            return [
                []
                if selector == self.missing
                else [[(990, 1 if selector["__name__"] == "pg_up" else 0)]]
                for selector in selectors
            ]

    transport = Transport()
    checker.check(transport, "application", now=1000)
    for selector in checker.SOURCES["application"]:
        if selector["__name__"].startswith("findme_gallery_search_"):
            transport.missing = selector
            with pytest.raises(checker.control.ControlError, match="expected sample missing"):
                checker.check(transport, "application", now=1000)
