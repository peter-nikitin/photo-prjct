import json

from prometheus_client import CollectorRegistry

from config.tests.test_queue_telemetry import samples


def test_terminal_outcomes_and_measured_durations_have_distinct_populations():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    labels = {"queue": "commerce_payment"}

    def record(outcome, timings):
        telemetry.record(
            json.dumps(
                {
                    "event": "queue.work.result",
                    "version": 1,
                    **labels,
                    "outcome": outcome,
                    "segments_ms": timings,
                }
            )
        )

    def value(metric, **extra):
        return samples(registry)[
            (f"findme_queue_{metric}", tuple(sorted((labels | extra).items())))
        ]

    assert value("results_total", outcome="success") == 0
    record("success", {"wait": 1000, "gateway": 200, "accept": 300, "interval": 500})
    record("success", {"wait": 2000})
    record("failed", None)
    record("canceled", None)
    assert value("results_total", outcome="success") == 2
    assert value("results_total", outcome="failed") == 1
    assert value("results_total", outcome="canceled") == 1
    assert value("duration_observations_total") == 1
    assert value("segment_duration_seconds_total", segment="gateway") == 0.2
    assert samples(registry)["findme_queue_results_listener_connected", ()] == 0
    assert value("results_observed_timestamp_seconds") > 0
    telemetry.set_listening(True)
    assert samples(registry)["findme_queue_results_listener_connected", ()] == 1


def test_work_failure_is_independent_of_listener_state(monkeypatch):
    from django.db import DatabaseError

    from config import queue_work

    def unreadable(*args, **kwargs):
        raise DatabaseError("unreadable")

    monkeypatch.setattr(queue_work, "_apply_delta", unreadable)
    callbacks = []

    def capture(callback, *, robust):
        callbacks.append((callback, robust))

    monkeypatch.setattr(queue_work.transaction, "on_commit", capture)
    queue_work.schedule_transition("commerce_email", "processing", "failed")
    assert len(callbacks) == 2
    assert all(robust for _, robust in callbacks)


def test_worker_segments_exclude_retries_and_require_worker_boundaries():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from config.queue_timings import worker_segments

    start = datetime(2026, 1, 1, tzinfo=UTC)
    accepted = SimpleNamespace(
        claimed_at=start + timedelta(seconds=5),
        download_duration_ms=1000,
        compute_duration_ms=2000,
        total_duration_ms=4000,
    )
    assert worker_segments(
        start, start + timedelta(seconds=1), accepted, start + timedelta(seconds=10)
    ) == {
        "wait": 1000,
        "retry": 4000,
        "download": 1000,
        "compute": 2000,
        "accept": 2000,
    }
    accepted.total_duration_ms = None
    assert (
        worker_segments(
            start, start + timedelta(seconds=1), accepted, start + timedelta(seconds=10)
        )
        is None
    )
