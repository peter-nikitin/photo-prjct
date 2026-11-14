"""Validate and aggregate the queue completion protocol without database access."""

import json
import re
from collections.abc import Iterator
from threading import Lock

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from config.queue_telemetry_protocol import COMPLETED_EVENT, VERSION

MAX_PAYLOAD_BYTES = 2048
MAX_QUEUES = 32
MAX_SEGMENTS = 16
MAX_SEGMENT_MS = 2**31 - 1
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate event key")
    return result


def _parse(payload: str) -> tuple[str, dict[str, int]] | None:
    if not payload.isascii() or len(payload) > MAX_PAYLOAD_BYTES:
        return None
    try:
        data = json.loads(payload, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.keys() != {"event", "version", "queue", "segments_ms"}:
        return None
    queue, version, segments = data["queue"], data["version"], data["segments_ms"]
    if (
        data["event"] != COMPLETED_EVENT
        or type(version) is not int
        or version != VERSION
        or not isinstance(queue, str)
        or not _NAME.fullmatch(queue)
        or not isinstance(segments, dict)
        or not 1 <= len(segments) <= MAX_SEGMENTS
    ):
        return None
    if any(
        not _NAME.fullmatch(name) or type(value) is not int or not 0 <= value <= MAX_SEGMENT_MS
        for name, value in segments.items()
    ):
        return None
    return queue, segments


class QueueTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._listening = False
        self._counts: dict[str, int] = {}
        self._durations_ms: dict[str, dict[str, int]] = {}

    def set_listening(self, listening: bool) -> None:
        with self._lock:
            self._listening = listening

    def record(self, payload: str) -> None:
        parsed = _parse(payload)
        if parsed is None:
            return
        queue, segments = parsed
        with self._lock:
            if queue not in self._counts:
                if len(self._counts) >= MAX_QUEUES:
                    return
                self._counts[queue] = 1
                self._durations_ms[queue] = segments
                return
            totals = self._durations_ms[queue]
            if totals.keys() != segments.keys():
                return
            self._counts[queue] += 1
            for name, duration in segments.items():
                totals[name] += duration

    def collect(self) -> Iterator[Metric]:
        with self._lock:
            listening = self._listening
            counts = self._counts.copy()
            durations_ms = {queue: totals.copy() for queue, totals in self._durations_ms.items()}

        completed = CounterMetricFamily(
            "findme_queue_completed", "Observed successful queue completions.", labels=["queue"]
        )
        duration = CounterMetricFamily(
            "findme_queue_segment_duration_seconds",
            "Elapsed segment time across observed successful queue completions.",
            labels=["queue", "segment"],
        )
        for queue, count in counts.items():
            completed.add_metric([queue], count)
            for segment, total in durations_ms[queue].items():
                duration.add_metric([queue, segment], total / 1000)

        yield GaugeMetricFamily(
            "findme_queue_telemetry_listening",
            "Whether the queue telemetry collector is subscribed to PostgreSQL.",
            value=int(listening),
        )
        yield completed
        yield duration
