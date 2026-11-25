"""Validate and aggregate the queue completion protocol without database access."""

import json
import time
from collections.abc import Iterator
from threading import Lock
from typing import Any

from prometheus_client import Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from config.queue_catalog import CYCLE_DURATION_BUCKETS, CYCLE_DURATION_METRIC, QUEUES
from config.queue_telemetry_protocol import (
    COMPLETED_EVENT,
    COMPLETION_VERSION,
    OUTCOMES,
    PHOTO_IMPORT_QUEUES,
    RESULT_EVENT,
    RESULT_VERSION,
)

SEGMENTS = {queue.key: frozenset(segment.key for segment in queue.segments) for queue in QUEUES}

MAX_PAYLOAD_BYTES = 2048
MAX_SEGMENTS = 16
MAX_SEGMENT_MS = 2**31 - 1
JOURNEYS = PHOTO_IMPORT_QUEUES | {"gallery_search", "selfie_search"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate event key")
    return result


def _decode(payload: str) -> dict[str, Any] | None:
    if not payload.isascii() or len(payload) > MAX_PAYLOAD_BYTES:
        return None
    try:
        data = json.loads(payload, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        return None
    return data


def _parse_completion(data: dict[str, Any]) -> tuple[str, dict[str, int]] | None:
    if data.keys() != {"event", "version", "queue", "segments_ms"} or (
        data["version"] != COMPLETION_VERSION
    ):
        return None
    queue, segments = data["queue"], data["segments_ms"]
    if (
        not isinstance(queue, str)
        or queue not in JOURNEYS
        or not isinstance(segments, dict)
        or not 1 <= len(segments) <= MAX_SEGMENTS
    ):
        return None
    if any(
        len(name) > 32
        or not name.isidentifier()
        or type(value) is not int
        or not 0 <= value <= MAX_SEGMENT_MS
        for name, value in segments.items()
    ):
        return None
    return queue, segments


class QueueTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._listening = False
        self._results = {queue: dict.fromkeys(OUTCOMES, 0) for queue in SEGMENTS}
        self._observations = dict.fromkeys(SEGMENTS, 0)
        self._segments_ms = {
            queue: dict.fromkeys(segments, 0) for queue, segments in SEGMENTS.items()
        }
        self._cycle_duration = Histogram(
            CYCLE_DURATION_METRIC,
            "Elapsed time of successful cycles with complete timings.",
            labelnames=("queue",),
            buckets=tuple(map(float, CYCLE_DURATION_BUCKETS)),
            registry=None,
        )
        for queue in SEGMENTS:
            self._cycle_duration.labels(queue)
        self._counts: dict[str, int] = {}
        self._durations_ms: dict[str, dict[str, int]] = {}

    def set_listening(self, listening: bool) -> None:
        with self._lock:
            self._listening = listening

    def record(self, payload: str) -> None:
        data = _decode(payload)
        if data is None:
            return
        if data.get("event") == RESULT_EVENT:
            self._record_result(data)
        elif data.get("event") == COMPLETED_EVENT:
            completion = _parse_completion(data)
            if completion is not None:
                self._record_completion(*completion)

    def _record_result(self, data: dict[str, Any]) -> None:
        fields = {"event", "version", "queue", "outcome", "segments_ms"}
        if data.keys() not in (fields, fields | {"count"}):
            return
        queue, outcome, timings = data["queue"], data["outcome"], data["segments_ms"]
        count = data.get("count", 1)
        if (
            data["version"] != RESULT_VERSION
            or not isinstance(queue, str)
            or queue not in SEGMENTS
            or outcome not in OUTCOMES
            or type(count) is not int
            or not 1 <= count <= 2**31 - 1
        ):
            return
        with self._lock:
            self._results[queue][outcome] += count
            if (
                outcome == "success"
                and count == 1
                and isinstance(timings, dict)
                and timings.keys() == SEGMENTS[queue]
                and all(type(ms) is int and 0 <= ms <= MAX_SEGMENT_MS for ms in timings.values())
            ):
                self._observations[queue] += 1
                self._cycle_duration.labels(queue).observe(sum(timings.values()) / 1000)
                for segment, ms in timings.items():
                    self._segments_ms[queue][segment] += ms

    def _record_completion(self, queue: str, segments_ms: dict[str, int]) -> None:
        with self._lock:
            if queue not in self._counts:
                self._counts[queue] = 1
                self._durations_ms[queue] = segments_ms
                return
            totals = self._durations_ms[queue]
            if totals.keys() != segments_ms.keys():
                return
            self._counts[queue] += 1
            for name, duration in segments_ms.items():
                totals[name] += duration

    def collect(self) -> Iterator[Metric]:
        with self._lock:
            listening = self._listening
            results = {queue: outcomes.copy() for queue, outcomes in self._results.items()}
            observations = self._observations.copy()
            cycle_duration = list(self._cycle_duration.collect())
            segments_ms = {queue: segments.copy() for queue, segments in self._segments_ms.items()}
            counts = self._counts.copy()
            durations_ms = {queue: totals.copy() for queue, totals in self._durations_ms.items()}

        completed = CounterMetricFamily(
            "findme_journey_completed", "Observed successful user journeys.", labels=["queue"]
        )
        duration = CounterMetricFamily(
            "findme_journey_segment_duration_seconds",
            "Elapsed segment time across observed successful queue completions.",
            labels=["queue", "segment"],
        )
        for queue, count in counts.items():
            completed.add_metric([queue], count)
            for segment, total in durations_ms[queue].items():
                duration.add_metric([queue, segment], total / 1000)

        yield completed
        yield duration

        listener = GaugeMetricFamily(
            "findme_queue_results_listener_connected",
            "Whether the result listener is subscribed.",
            value=int(listening),
        )
        observed = GaugeMetricFamily(
            "findme_queue_results_observed_timestamp_seconds",
            "Time of the result source scrape, including disconnections.",
            labels=["queue"],
        )
        result = CounterMetricFamily(
            "findme_queue_results", "Observed terminal queue outcomes.", labels=["queue", "outcome"]
        )
        measurement = CounterMetricFamily(
            "findme_queue_duration_observations",
            "Successful cycles with complete timings.",
            labels=["queue"],
        )
        timings = CounterMetricFamily(
            "findme_queue_segment_duration_seconds",
            "Segment time of successful measured cycles.",
            labels=["queue", "segment"],
        )
        at = time.time()
        for queue, outcomes in results.items():
            observed.add_metric([queue], at)
            for outcome, count in outcomes.items():
                result.add_metric([queue, outcome], count)
            measurement.add_metric([queue], observations[queue])
            for segment, total in segments_ms[queue].items():
                timings.add_metric([queue, segment], total / 1000)
        yield listener
        yield observed
        yield result
        yield measurement
        yield timings
        yield from cycle_duration
