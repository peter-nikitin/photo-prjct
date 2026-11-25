import json
from threading import Event, Thread

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families


def event(queue="gallery_search", segments=None, **overrides):
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


def test_journey_preserves_first_accepted_segment_set():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    telemetry.record(event())
    telemetry.record(event(segments={"work": 2250, "wait": 750}))
    telemetry.record(event(segments={"work": 1000}))
    telemetry.record(event(segments={"wait": 1000, "work": 1000, "extra": 1000}))

    assert {
        key: value
        for key, value in samples(registry).items()
        if key[0].startswith("findme_journey_")
    } == {
        ("findme_journey_completed_total", (("queue", "gallery_search"),)): 2,
        (
            "findme_journey_segment_duration_seconds_total",
            (("queue", "gallery_search"), ("segment", "wait")),
        ): 2,
        (
            "findme_journey_segment_duration_seconds_total",
            (("queue", "gallery_search"), ("segment", "work")),
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
        event(queue="q" * 97),
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
    assert not any(metric.startswith("findme_journey_") for metric, _ in samples(registry))


def test_unknown_journey_does_not_create_a_series():
    from config.queue_telemetry import QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    telemetry.record(event(queue="unknown"))
    assert not any(metric.startswith("findme_journey_") for metric, _ in samples(registry))


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
                if metric == "findme_journey_completed_total":
                    count = value
                elif (
                    metric == "findme_journey_segment_duration_seconds_total"
                    and dict(labels).get("segment") == "wait"
                ):
                    wait = value
                elif (
                    metric == "findme_journey_segment_duration_seconds_total"
                    and dict(labels).get("segment") == "work"
                ):
                    work = value
            assert count is not None and wait == count and work == 2 * count
    finally:
        writer.join(timeout=3)


@pytest.mark.django_db(transaction=True)
def test_photo_import_scrape_reads_only_counter_rows_and_clamps_display():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from processing.models import PhotoImportCounter

    from config.photo_import_metrics import PhotoImportCounters

    PhotoImportCounter.objects.update_or_create(
        pk="photo_import_gallery", defaults={"pending": -2, "failed": 3, "initialized": True}
    )
    PhotoImportCounter.objects.update_or_create(
        pk="photo_import_face", defaults={"pending": 2, "failed": 4, "initialized": False}
    )
    registry = CollectorRegistry()
    registry.register(PhotoImportCounters())
    assert samples(registry) == {("findme_photo_import_counters_ready", ()): 0}
    PhotoImportCounter.objects.filter(pk="photo_import_face").update(initialized=True)
    with CaptureQueriesContext(connection) as queries:
        observed = samples(registry)

    assert observed == {
        ("findme_photo_import_counters_ready", ()): 1,
        ("findme_photo_import_pending_count", (("queue", "photo_import_gallery"),)): 0,
        ("findme_photo_import_failed_count", (("queue", "photo_import_gallery"),)): 0,
        ("findme_photo_import_pending_count", (("queue", "photo_import_face"),)): 2,
        ("findme_photo_import_failed_count", (("queue", "photo_import_face"),)): 2,
    }
    reads = [query["sql"] for query in queries if query["sql"].startswith("SELECT")]
    assert len(reads) == 1
    assert 'FROM "processing_photoimportcounter"' in reads[0]
    assert "JOIN" not in reads[0]
    assert connection.connection is None
    assert PhotoImportCounter.objects.get(pk="photo_import_gallery").pending == -2


def test_full_cycle_histogram_measures_only_individual_successes_with_complete_timings():
    from config.queue_catalog import CYCLE_DURATION_BUCKETS, QUEUES
    from config.queue_telemetry import MAX_SEGMENT_MS, MAX_SEGMENTS, QueueTelemetry

    telemetry = QueueTelemetry()
    registry = CollectorRegistry()
    registry.register(telemetry)
    queue = QUEUES[0]
    labels = (("queue", queue.key),)
    metric = "findme_queue_cycle_duration_seconds"
    timings = dict.fromkeys((segment.key for segment in queue.segments), 1000)

    def record(outcome="success", segments=timings, count=1):
        telemetry.record(
            json.dumps(
                {
                    "event": "queue.work.result",
                    "version": 1,
                    "queue": queue.key,
                    "outcome": outcome,
                    "segments_ms": segments,
                    "count": count,
                }
            )
        )

    initial = samples(registry)
    assert {metric_labels for name, metric_labels in initial if name == metric + "_bucket"} == {
        (("le", bound), ("queue", definition.key))
        for definition in QUEUES
        for bound in (*CYCLE_DURATION_BUCKETS, "+Inf")
    }
    assert initial[(metric + "_count", labels)] == 0
    record(outcome="failed")
    record(outcome="canceled")
    record(segments={})
    record(count=2)
    record(segments={**timings, queue.segments[0].key: -1})
    record()
    record(segments=dict.fromkeys(timings, MAX_SEGMENT_MS))
    observed = samples(registry)

    assert observed[(metric + "_count", labels)] == 2
    assert observed[(metric + "_sum", labels)] == pytest.approx(
        len(timings) * (1 + MAX_SEGMENT_MS / 1000)
    )
    assert observed[("findme_queue_duration_observations_total", labels)] == 2
    assert observed[(metric + "_bucket", (("le", "0.5"), *labels))] == 0
    assert observed[(metric + "_bucket", (("le", "10.0"), *labels))] == 1
    finite_buckets = [
        (float(dict(metric_labels)["le"]), value)
        for (name, metric_labels), value in observed.items()
        if name == metric + "_bucket"
        and dict(metric_labels)["queue"] == queue.key
        and dict(metric_labels)["le"] != "+Inf"
    ]
    upper_bound, count = max(finite_buckets)
    assert upper_bound >= MAX_SEGMENTS * MAX_SEGMENT_MS / 1000
    assert count == 2
    assert observed[(metric + "_count", (("queue", QUEUES[1].key),))] == 0
