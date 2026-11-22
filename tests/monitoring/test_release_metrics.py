import importlib.util
from pathlib import Path
from types import SimpleNamespace

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
    }

    class Transport:
        def read_samples(self, selectors, *, now):
            return [
                [[(990, samples[key])]] if key in samples else []
                for selector in selectors
                for key in [
                    (
                        selector["__name__"],
                        selector["job"],
                        selector.get("stage"),
                    )
                ]
            ]

    checker.check(Transport(), "application", now=1000)


@pytest.mark.parametrize(
    ("listening", "ready", "counts", "valid"),
    [
        (1, 0, None, False),
        (1, 1, 0, True),
        (0, 0, None, False),
        (None, 0, None, False),
        (1, None, None, False),
        (1, 2, None, False),
        (1, 1, None, False),
    ],
)
def test_queue_telemetry_requires_initialized_counter_series(listening, ready, counts, valid):
    checker = load("release_queue_telemetry", "deploy/monitoring/prometheus/verify_metrics.py")

    class Transport:
        def read_samples(self, selectors, *, now):
            rows = []
            for selector in selectors:
                assert selector["job"] == "findme-queue-telemetry"
                value = {
                    "findme_queue_results_listener_connected": listening,
                    "findme_photo_import_counters_ready": ready,
                    "findme_photo_import_pending_count": counts,
                    "findme_photo_import_failed_count": counts,
                }[selector["__name__"]]
                rows.append([[(1995, value)]] if value is not None else [])
            return rows

    if valid:
        checker.check(Transport(), "queue-telemetry", now=2000)
    else:
        with pytest.raises(checker.control.ControlError):
            checker.check(Transport(), "queue-telemetry", now=2000)


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


@pytest.mark.parametrize(
    ("scope", "available_at", "result", "elapsed_expected"),
    [
        ("queue-telemetry", 30, 0, 30),
        ("queue-telemetry", 310, 1, 300),
        ("application", 330, 1, 300),
        ("vm", 330, 1, 300),
    ],
)
def test_cli_waits_for_fresh_samples_with_a_bounded_retry(
    monkeypatch, scope, available_at, result, elapsed_expected
):
    import sys

    checker = load(
        "release_metrics_initial_snapshot", "deploy/monitoring/prometheus/verify_metrics.py"
    )
    elapsed = 0

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    class Transport:
        def read_samples(self, selectors, *, now):
            rows = []
            for selector in selectors:
                name = selector["__name__"]
                if elapsed < available_at and name != "findme_queue_results_listener_connected":
                    rows.append([])
                    continue
                timestamp = 2000 + elapsed
                rows.append([[(timestamp, 1)]])
            return rows

    monkeypatch.setattr(sys, "argv", ["verify_metrics.py", scope])
    monkeypatch.setattr(checker.control, "identity", lambda *_args: "token")
    monkeypatch.setattr(checker.control, "CloudTransport", lambda *_args: Transport())
    monkeypatch.setattr(
        checker,
        "time",
        SimpleNamespace(monotonic=lambda: elapsed, time=lambda: 2000 + elapsed, sleep=sleep),
    )
    assert checker.main() == result
    assert elapsed == elapsed_expected


@pytest.mark.parametrize("failure", [None, "unavailable", "missing", "stale"])
def test_queue_work_requires_a_fresh_series_for_every_queue_and_state(failure):
    checker = load("release_queue_work", "deploy/monitoring/prometheus/verify_metrics.py")
    queues = set(checker.WORK_QUEUES)
    states = {"waiting", "processing", "retry_wait", "paused"}

    class Transport:
        def read_samples(self, selectors, *, now):
            assert {selector["job"] for selector in selectors} == {
                "findme-queue-telemetry",
                "findme-http",
            }
            assert {
                selector["queue"]
                for selector in selectors
                if selector["__name__"] == "findme_queue_work_available"
            } == queues
            assert {
                (selector["queue"], selector["state"])
                for selector in selectors
                if selector["__name__"] == "findme_queue_work_count"
            } == {(queue, state) for queue in queues for state in states}
            results = []
            for selector in selectors:
                availability = selector["__name__"] == "findme_queue_work_available"
                if (
                    availability
                    and selector["queue"] == "photo_face_embedding"
                    and failure == "unavailable"
                ):
                    results.append([[(1995, 0)]])
                elif (
                    selector.get("state") == "processing"
                    and selector["queue"] == "commerce_payment"
                ):
                    if failure == "missing":
                        results.append([])
                    else:
                        results.append([[(1600 if failure == "stale" else 1995, 1)]])
                else:
                    observed = selector["__name__"].endswith("_observed_timestamp_seconds")
                    results.append([[(1995, 1995 if observed else 1 if availability else 0)]])
            return results

    if failure is None:
        checker.check(Transport(), "queue-work", now=2000)
    else:
        with pytest.raises(checker.control.ControlError):
            checker.check(Transport(), "queue-work", now=2000)
