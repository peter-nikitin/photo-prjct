import json
from threading import Event, Thread

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families


def event(queue="another_queue", segments=None, **overrides):
    return json.dumps(
        {
            "event": "queue.task.completed",
            "version": 1,
            "queue": queue,
            "segments_ms": {"wait": 1250, "work": 2750} if segments is None else segments,
            **overrides,
        }
    )


def samples(registry):
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(generate_latest(registry).decode())
        for sample in family.samples
    }


def test_learns_new_queue_and_preserves_first_segment_set():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    telemetry.record(event())
    telemetry.record(event(segments={"work": 2250, "wait": 750}))
    telemetry.record(event(segments={"work": 1000}))
    telemetry.record(event(segments={"wait": 1000, "work": 1000, "extra": 1000}))

    assert samples(registry) == {
        ("findme_queue_telemetry_listening", ()): 0,
        ("findme_queue_completed_total", (("queue", "another_queue"),)): 2,
        (
            "findme_queue_segment_duration_seconds_total",
            (("queue", "another_queue"), ("segment", "wait")),
        ): 2,
        (
            "findme_queue_segment_duration_seconds_total",
            (("queue", "another_queue"), ("segment", "work")),
        ): 5,
    }


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        event(event="queue.task.failed"),
        event(version=2),
        event(version=True),
        event(version=1.0),
        event(result_id="private"),
        event(queue="queue name"),
        event(queue="q" * 33),
        event(queue=["not-a-string"]),
        event(segments={}),
        event(segments=["wait"]),
        event(segments={"wait": True}),
        event(segments={"wait": -1}),
        event(segments={"wait": 0.5}),
        event(segments={"wait": float("nan")}),
        event(segments={"wait": 1 << 63}),
        event(segments={"s" * 33: 1}),
        event(segments={"bad-label": 1}),
        event(segments={f"stage_{i}": 0 for i in range(17)}),
        event() + " " * 2048,
        event().replace('"wait": 1250', '"wait": 1250, "wait": 1'),
    ],
)
def test_malformed_event_does_not_occupy_a_queue_or_change_metrics(payload):
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    telemetry.record(payload)
    assert samples(registry) == {("findme_queue_telemetry_listening", ()): 0}


def test_full_collector_keeps_counting_existing_queues():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    for index in range(33):
        telemetry.record(event(queue=f"queue_{index}"))
    telemetry.record(event(queue="queue_0"))

    counts = {
        dict(labels)["queue"]: value
        for (metric, labels), value in samples(registry).items()
        if metric == "findme_queue_completed_total"
    }
    assert len(counts) == 32
    assert "queue_32" not in counts
    assert counts["queue_0"] == 2


def test_scrape_uses_one_snapshot_for_count_and_timing_series():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    payload = event(segments={"wait": 1000, "work": 2000})
    telemetry.record(payload)
    started = Event()

    def record():
        started.set()
        for _ in range(2000):
            telemetry.record(payload)

    writer = Thread(target=record)
    writer.start()
    try:
        assert started.wait(timeout=1)
        for _ in range(100):
            count = wait = work = None
            for (metric, labels), value in samples(registry).items():
                if metric == "findme_queue_completed_total":
                    count = value
                elif dict(labels).get("segment") == "wait":
                    wait = value
                elif dict(labels).get("segment") == "work":
                    work = value
            assert count is not None and wait == count and work == 2 * count
    finally:
        writer.join(timeout=3)


def test_backlog_changes_use_a_memory_snapshot_and_reject_unbounded_events():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    counts = {"photo_import_gallery": (4, 1), "photo_import_face": (7, 0)}
    payload = json.dumps(
        {
            "event": "queue.backlog.changed",
            "version": 2,
            "photo_id": "a" * 32,
            "deltas": {"photo_import_gallery": [-1, -1], "photo_import_face": [0, 1]},
        }
    )
    telemetry.record(payload)
    assert not any(metric.startswith("findme_photo_import_") for metric, _ in samples(registry))

    assert telemetry._backlogs["photo_import_gallery"] == (-1, -1)
    telemetry.set_listening(True)
    draft = telemetry.start_backlog_reconciliation()
    assert draft is not None
    assert telemetry.set_backlog_reconciliation_upper_bound(draft, "e")
    assert telemetry.add_backlog_reconciliation_page(draft, after="e", counts=counts)
    telemetry.finish_backlog_reconciliation(draft, reconciled_at=1234)
    telemetry.record(event())
    for updated in (
        payload.replace("photo_import_face", "arbitrary_queue"),
        payload.replace("[-1, -1]", "[-2, -1]"),
        payload.replace("[0, 1]", "[0, true]"),
        payload.replace("[0, 1]", "[0, 1, 2]"),
        payload.replace("[0, 1]", "[]"),
        payload.replace('"version": 2', '"version": 1'),
        payload.replace("a" * 32, "a" * 33),
        payload.replace("a" * 32, "unwanted photo id"),
    ):
        telemetry.record(updated)
    assert (
        samples(registry)[
            ("findme_photo_import_pending_count", (("queue", "photo_import_gallery"),))
        ]
        == 4
    )
    telemetry.record(payload)

    observed = samples(registry)
    assert observed[("findme_photo_import_last_reconciliation_timestamp_seconds", ())] == 1234
    assert (
        observed[("findme_photo_import_pending_count", (("queue", "photo_import_gallery"),))] == 3
    )
    assert observed[("findme_photo_import_failed_count", (("queue", "photo_import_gallery"),))] == 0
    assert observed[("findme_photo_import_failed_count", (("queue", "photo_import_face"),))] == 1
    assert observed[("findme_queue_completed_total", (("queue", "another_queue"),))] == 1


def test_reconciliation_uses_only_deltas_beyond_bound_or_behind_cursor():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    telemetry.set_listening(True)
    draft = telemetry.start_backlog_reconciliation()
    assert draft is not None
    assert telemetry.set_backlog_reconciliation_upper_bound(draft, "8")

    def change(photo_id, pending):
        telemetry.record(
            json.dumps(
                {
                    "event": "queue.backlog.changed",
                    "version": 2,
                    "photo_id": photo_id,
                    "deltas": {"photo_import_gallery": [pending, 0]},
                }
            )
        )

    change("9", 1)  # A new photo beyond the fixed upper bound is not in any page.
    assert telemetry.add_backlog_reconciliation_page(
        draft, after="4", counts={"photo_import_gallery": (1, 0)}
    )
    change("1", -1)  # Completion during the next page, after its earlier page was counted.
    change("2", 1)  # Insertion behind the cursor.
    change("5", 1)  # An unread photo will be included in the next page.
    assert telemetry.add_backlog_reconciliation_page(
        draft, after="8", counts={"photo_import_gallery": (2, 0)}
    )
    telemetry.finish_backlog_reconciliation(draft, reconciled_at=1234)
    observed = samples(registry)
    assert (
        observed[("findme_photo_import_pending_count", (("queue", "photo_import_gallery"),))] == 4
    )
    assert all("photo_id" not in dict(labels) for _, labels in observed)
